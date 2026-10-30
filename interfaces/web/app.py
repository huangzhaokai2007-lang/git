'卡 16：交互层聊天界面（Streamlit），业务一律在 agent/ 层。'

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
for _path in (Path(__file__).resolve().parent, ROOT):          # 同目录组件 + 仓库根（包导入）
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
os.environ.setdefault("DB_PATH", str(ROOT / "var" / "bank.db"))  # 只设环境变量，不 import data/（卡 20-C：库住 var/，别写进代码目录 data/）

from dotenv import load_dotenv                                  # noqa: E402  （.env 定位与 cwd 无关）
from agent import balance_flow, card_binding_flow, confirm_card, orchestrator, write_flow  # noqa: E402

import components as ui                                         # noqa: E402
import card_binding_ui as cards_ui                               # noqa: E402
import card_limit_ui as limits_ui                                # noqa: E402
import card_lock_ui as locks_ui                                  # noqa: E402
import card_lost_ui as lost_ui                                   # noqa: E402
import forms                                                     # noqa: E402  （F10：表单桥拆出，含风险测评问卷）
import card_edit_ui
import premium_ui                                               # noqa: E402  （管线条/依据/示例/动效）
import workspace_ui                                             # noqa: E402

load_dotenv(ROOT / ".env")

GREETING = "您好 请问我能为您提供什么帮助"

HISTORY_TURNS = 4
# ---------------- 启动引导（只碰环境变量与 data/ 层自己的 CLI） ----------------

def _ensure_db() -> bool:
    """确保合成库存在；缺了就调 `python -m data.seed` 生成（界面不 import data/、不写 SQL）。"""
    db = Path(os.environ["DB_PATH"])
    if db.exists() and db.stat().st_size > 0:
        return True
    with st.spinner("首次启动：生成合成数据（`python -m data.seed`）…"):
        done = subprocess.run([sys.executable, "-m", "data.seed"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        st.error("合成数据生成失败，请先在仓库根目录跑 `uv run python -m data.seed`：\n\n"
                 f"```\n{(done.stderr or done.stdout)[-600:]}\n```")
        return False
    st.success("合成数据已生成。")
    return True

def _init_state() -> None:
    """会话态：对话、轨迹、余额回显、追问轮次（跨 rerun 保留）。"""
    state = st.session_state
    if state.pop('navigate-page', None) == '💳 我的银行卡':
        state['bound-card-query'] = True
    state.pop('page', None)
    state.setdefault("session_id", f"web-{uuid.uuid4().hex[:8]}")
    state.setdefault("messages", [{"role": "assistant", "text": GREETING, "turn": None}])
    state.setdefault('workspace-start', 0)
    state.setdefault("turns", [])
    state.setdefault("balance", [])
    state.setdefault("pending_id", None)
    state.setdefault("clarify_round", 0)
    state.setdefault("bound-payee-query", False)
    state.setdefault("bound-schedule-query", False)
    state.setdefault("bound-aa-query", False)
    state.setdefault("bound-gift-query", False)

@st.cache_resource
def _agent_executor() -> ThreadPoolExecutor:
    '编排层调用统一跑在这条**常驻线程**上（进程级单例）。'
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")

# ---------------- 与编排层的唯一入口 ----------------
def _ask(text: str, *, display: str | None = None, extension: bool = False) -> None:
    '把一句话交给编排层，记录回执与轨迹（界面不解释业务、不派生数字）。'
    state = st.session_state
    if not extension:
        workspace_ui.navigation.clear()
    workspace_ui.remember_view(_agent_executor(), state['session_id'])
    display = display or _agent_executor().submit(card_binding_flow.safe_chat_text, text).result()
    state["messages"].append({"role": "user", "text": display or text})
    try:
        workspace = state["messages"][state.get('workspace-start', 0):]
        history = [m["text"] for m in workspace[-(HISTORY_TURNS + 1):-1] if m.get("text")]
        with st.spinner('正在处理，请稍候…'):
            turn = _agent_executor().submit(card_binding_flow.handle, text, history=history or None,
                                            clarify_round=state["clarify_round"],
                                            session_id=state["session_id"]).result()
    except Exception:                    # noqa: BLE001 —— Keep the last result and offer safe read retry.
        failure = '这次请求暂时未能完成，请稍后重试。'
        workspace_ui.accept({'intent': None, 'reply': failure,
                             'error_code': 'SERVICE_UNAVAILABLE'}, text)
        state['messages'].append({'role': 'assistant', 'turn': None, 'text': failure})
        return
    #: 2026-10-29（用户要求）：群收款出确认卡时，原始文本卡不再刷进聊天 ——
    #: 卡面（a_card_ui）已经把金额/参与人/分摊说清楚了，两份重复很难看。
    #: 用 getattr 兜底：测试桩/降级路径可能给的不是完整 Turn（缺 intent/states）
    keep_text = not (getattr(turn, "intent", "") == "aa_collect"
                     and "CONFIRM_CARD" in (getattr(turn, "states", None) or ()))
    dump = turn.model_dump()
    workspace_ui.accept(dump, text)
    if turn.error_code:
        state['messages'].append({'role': 'assistant', 'text': turn.reply, 'turn': dump})
        state['turns'].append({'ts': time.strftime('%H:%M:%S'), 'text': display or text, 'turn': dump})
        return
    if turn.intent == "card_query" and not state.get('cards-view', {}).get('form'):
        cards_ui.reset(turn)
    if turn.ask in ("card_lock", "card_missing", "card_unlock"): limits_ui.reset()
    if turn.ask == "limit_adjust": locks_ui.reset()
    if turn.ask in ('card_lost', 'card_lost_choice', 'card_lost_records'):
        limits_ui.reset()
        locks_ui.reset()
    elif turn.ask in ('card_lock', 'card_missing', 'card_unlock', 'limit_adjust'):
        lost_ui.reset()
    lost_ui.accept_turn(_agent_executor(), state['session_id'], turn.ask)
    limits_ui.accept_turn(_agent_executor(), state["session_id"], text, turn.ask)
    locks_ui.accept_turn(_agent_executor(), state["session_id"], turn.ask)
    import scene_ui
    scene_ui.accept(_agent_executor(), state["session_id"], turn)
    premium_ui.toast_for(turn)                    # 成功/失败微反馈（只读回合不打扰）
    if keep_text:
        state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    else:                                   # 群收款确认回合：只留轨迹，文本不刷屏
        state["messages"].append({"role": "assistant", "text": "", "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display or text, "turn": dump})
    state["clarify_round"] = state["clarify_round"] + 1 if "CLARIFY" in turn.states else 0
    state["pending_id"] = turn.pending_id
    #: 2026-10-30（用户反馈「胡乱出现收款人解析」）：解析卡只在**它那次意图**下有效。
    #: 新的一轮换了别的事，就把过期解析卡清掉（它原本有 5 分钟 TTL，会一直挂在屏上）。
    from agent import payee_picker as _picker

    _pending = _picker.pending(st.session_state["session_id"])
    if _pending and _pending.get("intent") != turn.intent:
        _picker.clear(st.session_state["session_id"])
    #: 2026-10-31（用户：重新发起群收款时金额还留着上次的 390）：新的一句「我要 aa 收款」
    #: 属于**新起一笔** → 让卡面在下一轮开头把表单清空（不能在按钮/回调里直接清，
    #: 那会跟已实例化的输入框打架）。
    if (turn.intent == "aa_collect" and "CLASSIFY" in (turn.states or [])
            and not {"CONFIRM_CARD", "EXECUTE"} & set(turn.states or [])):
        state["aa-reset-form-pending"] = True
    state["bound-payee-query"] = turn.intent == "payee_list" and turn.error_code is None
    state["bound-schedule-query"] = turn.intent == "scheduled_list" and turn.error_code is None
    #: 2026-10-28：发起群收款（含缺槽追问）时也画卡面 —— 总额/选人/按人金额都在卡上填，
    #: 比让用户一句句打字快（用户要求）。
    state["bound-aa-query"] = (turn.intent in ("aa_list", "aa_collect")
                                and turn.error_code is None)
    state["bound-gift-query"] = turn.intent == "gift_order_list" and turn.error_code is None
    state['bound-card-query'] = state.get('bound-card-query', False) or (
        (turn.intent == 'card_query' or turn.ask == 'card_lock') and turn.error_code is None)
    if turn.intent == "balance_query" and not turn.error_code and turn.reply:
        state["balance"] = [{"text": turn.reply, "facts": turn.facts}]

def _follow_up(prompt: str, *, display: str | None = None, rerun: bool = True) -> None:
    if (prompt != '返回刚才的列表' and st.session_state.get('workspace-result', {}).get('prompt') == prompt
            and not st.session_state.get('workspace-error')):
        return
    if rerun and not st.session_state.get('workspace-error'):
        workspace_ui.navigation.push(_agent_executor(), st.session_state['session_id'])
    _ask(prompt, display=display, extension=rerun)
    if rerun:
        st.rerun()

def _cancel(confirmation) -> None:
    """取消这笔写操作：只清 agent 层的会话确认态 —— 不调工具、不执行（铁律 3）。"""
    confirm_card.clear(confirmation.session_id)
    if st.session_state.get('workspace-nav-stack'):
        workspace_ui.navigation.back(_agent_executor(), confirmation.session_id)
    else:
        workspace_ui.return_after_cancel(_agent_executor(), confirmation.session_id)
    st.session_state["messages"].append(
        {"role": "assistant", "text": "已取消这笔操作：未提交执行，没有产生任何资金变动。", "turn": None})

def _render_pending() -> None:
    """L3 待复核：登记仍在 agent 层，撤销也走 agent 层（界面只显示与转发）。"""
    pending_id = st.session_state.get("pending_id")
    last = st.session_state["messages"][-1]
    if not pending_id or last.get("turn") is None or last["turn"].get("pending_id") != pending_id:
        return
    if ui.pending_card(last["text"]):
        if confirm_card.revoke(pending_id, st.session_state["session_id"]):
            st.session_state["pending_id"] = None
            st.session_state["messages"].append(
                {"role": "assistant", "text": confirm_card.undone_text(pending_id), "turn": None})
        st.rerun()

def _confirm_submit(text: str, *, inline: bool, display: str | None = None) -> None:
    source = st.session_state.get('workspace-result')
    _ask(text, display=display)
    if inline:
        st.session_state['workspace-result'] = source
        st.session_state['bound-payee-query'] = True
        last = st.session_state['messages'][-1]
        turn = last.get('turn') or {}
        st.session_state['payee-inline-done'] = bool(turn.get('executed'))
        if turn.get('error_code') or turn.get('executed') or not turn:
            st.session_state['payee-edit-feedback'] = last.get('text', '')
        else:
            st.session_state.pop('payee-edit-feedback', None)
        st.session_state.pop('workspace-error', None)


def _cancel_inline_payee(confirmation) -> None:
    confirm_card.clear(confirmation.session_id)
    st.session_state['payee-edit-feedback'] = '已取消本次操作，未提交执行。'
    st.session_state.pop('payee-inline-intent', None)


def _render_confirm(*, inline: bool = False) -> None:
    """确认卡 / OTP 卡：状态取自 agent 层，动作回灌编排层。"""
    from agent import web_features
    _agent_executor().submit(web_features.clear_removed_drafts, st.session_state['session_id']).result()
    confirmation = confirm_card.current(st.session_state["session_id"])
    if confirmation is None:
        #: 2026-10-30：卡没了就把「修改」抽屉的展开态复位，否则下一张确认卡会自动展开
        st.session_state["card-edit-open"] = False
        return
    if confirmation.stage == "otp":
        action = ui.otp_card(confirmation, compact=inline)
        if action is not None:
            if action[0] == "cancel":
                _cancel_inline_payee(confirmation) if inline else _cancel(confirmation)
            elif action[0] == "submit":
                _confirm_submit(action[1], inline=inline, display="（短信验证码已提交，值不回显）")
            st.rerun()
        return
    #: 2026-10-30（用户要求）：修改面板做成**确认卡内部抽屉**（点「✏️ 修改」在这张卡里展开）
    #: 只有"有可编辑字段"的确认卡才给「修改」（黑名单确认卡之类点了也没用）
    from agent import card_draft

    _draft = _agent_executor().submit(card_draft.draft_of, st.session_state["session_id"]).result()
    _editable = bool(_draft is not None and getattr(_draft, "fields", None))
    action = ui.confirm_card(
        confirmation, compact=inline, editable=_editable,
        edit_panel=lambda: card_edit_ui.render(_agent_executor(), st.session_state["session_id"]))
    if action == "confirm":
        _confirm_submit("确认", inline=inline)
        st.rerun()
    if action == "cancel":
        _cancel_inline_payee(confirmation) if inline else _cancel(confirmation)
        st.rerun()

# ---------------- 表单桥（卡 20 收款人 / F10 风险测评） ----------------
# 两个表单的渲染与提交都拆到 `interfaces/web/forms.py`（F10 接通后本文件超红线 300）；
# 这里只负责"交给它、按结果 rerun"，会话状态仍由本模块唯一持有。

def _render_forms() -> None:
    """渲染全部表单桥；任一表单提交了 → rerun（与历史 `_render_payee_form` 的行为一致）。"""
    if len(st.session_state['messages']) <= st.session_state.get('workspace-start', 0):
        return
    if forms.render_all(st.session_state, _agent_executor()):
        st.rerun()

# ---------------- 侧边栏 ----------------

def _refresh_sidebar_balance():
    turn = _agent_executor().submit(balance_flow.refresh, st.session_state["session_id"]).result()
    if not turn.error_code:
        st.session_state["balance"] = [{"text": turn.reply, "facts": turn.facts}]
    return turn

def _sidebar() -> None:
    """侧边栏：账户余额、快捷场景与重置会话。"""
    with st.sidebar:
        st.markdown('<div class="bank-brand" role="heading" aria-level="1" aria-label="AI Banking Agent"><span aria-hidden="true">AI Banking</span><span aria-hidden="true">Agent</span></div>', unsafe_allow_html=True)
        st.divider()
        st.markdown("#### 账户余额")
        scope = (st.session_state["session_id"], _agent_executor().submit(card_binding_flow.owner_key).result())
        if st.session_state.get("sidebar-balance-scope") != scope:
            # Record the attempt even on failure; ordinary reruns must not retry.
            st.session_state["sidebar-balance-scope"] = scope
            st.session_state["balance"] = []
            turn = _refresh_sidebar_balance()
            if turn.error_code:
                st.error(turn.reply)
        if ui.balance_box(st.session_state["balance"]):
            turn = _refresh_sidebar_balance()
            if turn.error_code:
                st.error(turn.reply)
            else:
                st.rerun()
        st.divider()
        st.markdown("#### 快捷场景")
        prompt = ui.scenes()
        if prompt is not None and not workspace_ui.reuse_shortcut(prompt):
            import shortcut_ui
            workspace_ui.remember_view(_agent_executor(), st.session_state['session_id'])
            shortcut_ui.start(_agent_executor(), st.session_state['session_id'])
            if prompt != '调整额度' and st.session_state.get('limit-view'):
                st.session_state['limit-view']['open'] = False
            st.session_state['workspace-shortcut'] = prompt
            st.session_state.pop('workspace-result', None)
            st.session_state.pop('workspace-error', None)
            if prompt == '出一份上个月的账单报告':
                import legacy_bill_ui
                legacy_bill_ui.open_picker()
            elif prompt == '查看操作记录':
                st.session_state['operation-records-open'] = True
            else:
                _ask(prompt)
        elif prompt == '调整额度' and st.session_state.get('limit-view', {}).get('result'):
            limits_ui.open_request(_agent_executor(), st.session_state['session_id'], prompt)
            st.session_state['bound-card-query'] = False
        st.divider()
        if st.button("重置会话", icon=":material/restart_alt:", key="reset-session", width="stretch"):
            from agent import scene_flow
            _agent_executor().submit(scene_flow.clear, st.session_state["session_id"]).result()
            confirm_card.reset_state()
            cards_ui.reset()
            limits_ui.reset()
            locks_ui.reset()
            lost_ui.reset()
            workspace_ui.reset()
            for domain in ('card', 'payee', 'schedule', 'aa', 'gift', 'lost'):
                st.session_state[f'bound-{domain}-query'] = False
            st.session_state['operation-records-open'] = False
            st.session_state.pop("sidebar-balance-scope", None)
            for key, value in (("turns", []), ("balance", []), ("pending_id", None), ("clarify_round", 0), ('workspace-start', 0)):
                st.session_state[key] = value
            st.session_state["messages"] = [{"role": "assistant", "text": GREETING, "turn": None}]
            st.rerun()

def _chat_page() -> None:
    #: 网页里群收款要先出**可编辑的发起卡**（用户 2026-10-31），点「发起群收款」才出确认卡
    write_flow.set_aa_edit_first(True)
    workspace_ui.notifications(_agent_executor(), st.session_state['session_id'], _follow_up)
    workspace_ui.transcript(st.session_state['messages'], _agent_executor(),
                            st.session_state['session_id'], _follow_up)
    if (workspace_ui.current() not in ('', 'bills') or
            (not workspace_ui.current() and st.session_state.get('workspace-error'))):
        with st.container(key='current-business'):
            st.markdown('### 扩展查看' if st.session_state.get('workspace-nav-stack') else '### 当前业务')
            workspace_ui.panels(_agent_executor(), st.session_state['session_id'], _follow_up,
                                _render_pending, _render_forms, _render_confirm)
    if (text := st.chat_input("")) and text.strip():
        _ask(text)
        st.rerun()

def main() -> None:
    st.set_page_config(page_title="AI Banking Agent · 模拟环境", page_icon="🏦", layout="wide")
    st.markdown(ui.load_css(), unsafe_allow_html=True)
    premium_ui.inject_css()                                 # 卡片入场 / 等宽数字 / 卡头色条
    if not _ensure_db():
        return
    if not st.get_option('global.appTest'):
        from agent import card_lost_flow
        card_lost_flow.start_runner()
    _init_state()
    cards_ui.prepare(_agent_executor(), st.session_state["session_id"])
    _sidebar()
    _chat_page()

main()
