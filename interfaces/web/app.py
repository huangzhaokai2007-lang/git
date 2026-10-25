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
from agent import card_binding_flow, confirm_card, orchestrator  # noqa: E402

import components as ui                                         # noqa: E402
import card_binding_ui as cards_ui                               # noqa: E402
import card_limit_ui as limits_ui                                # noqa: E402
import card_lock_ui as locks_ui                                  # noqa: E402
import card_lost_ui as lost_ui                                   # noqa: E402
import forms                                                     # noqa: E402  （F10：表单桥拆出，含风险测评问卷）
import payee_card_ui as payee_ui                                 # noqa: E402  （T20：查看白名单的卡片渲染）
import insight_card_ui                                        # noqa: E402  （main：洞察卡）
import card_edit_ui                                             # noqa: E402  （S5：可编辑卡片）
import payee_picker_ui                                          # noqa: E402  （解析卡：同名多命中）
import schedule_card_ui                                         # noqa: E402  （预约卡面：执行/改时间/取消）
import aa_card_ui                                               # noqa: E402  （AA 卡面：登记已付/撤销）
import gift_card_ui                                             # noqa: E402  （送礼查询卡：支付/取消）
import audit_ui                                                 # noqa: E402  （审计回放：trace_id → 链路）
import activity_ui                                              # noqa: E402  （main：活动流）

load_dotenv(ROOT / ".env")

DEMO_SCRIPT = ("查一下余额", "上个月的流水", "上个月花了多少", "上个月有没有异常交易", "上个月的账单总结")
PULL_PERIODS = ("本月", "上个月", "上上个月")
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
def _ask(text: str, *, display: str | None = None) -> None:
    '把一句话交给编排层，记录回执与轨迹（界面不解释业务、不派生数字）。'
    state = st.session_state
    display = display or _agent_executor().submit(card_binding_flow.safe_chat_text, text).result()
    state["messages"].append({"role": "user", "text": display or text})
    try:
        history = [m["text"] for m in state["messages"][-(HISTORY_TURNS + 1):-1] if m.get("text")]
        turn = _agent_executor().submit(card_binding_flow.handle, text, history=history or None,
                                        clarify_round=state["clarify_round"],
                                        session_id=state["session_id"]).result()
    except Exception as exc:                    # noqa: BLE001 —— 不吞异常：如实展示并给处置建议
        state["messages"].append({"role": "assistant", "turn": None,
                                  "text": f"这次请求没能完成（{type(exc).__name__}: {exc}）。"
                                          "如果这是库结构问题（例如旧库缺表），在仓库根目录跑 "
                                          "`uv run python -m data.seed --reset` 重新生成合成数据后再试。"})
        return
    dump = turn.model_dump()
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
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display or text, "turn": dump})
    state["clarify_round"] = state["clarify_round"] + 1 if "CLARIFY" in turn.states else 0
    state["pending_id"] = turn.pending_id
    state["bound-payee-query"] = turn.intent == "payee_list" and turn.error_code is None
    state["bound-schedule-query"] = turn.intent == "scheduled_list" and turn.error_code is None
    state["bound-aa-query"] = turn.intent == "aa_list" and turn.error_code is None
    state["bound-gift-query"] = turn.intent == "gift_order_list" and turn.error_code is None
    state['bound-card-query'] = state.get('bound-card-query', False) or (
        (turn.intent == 'card_query' or turn.ask == 'card_lock') and turn.error_code is None)
    if turn.intent == "balance_query" and not turn.error_code and turn.reply:
        state["balance"] = [item for item in state["balance"] if item["text"] != turn.reply][-1:]
        state["balance"].append({"text": turn.reply, "trace_id": turn.trace_id})


def _follow_up(prompt: str) -> None:
    _ask(prompt)
    st.rerun()


def _insight_card(turn, key: str) -> bool:
    return insight_card_ui.render(turn, key=key, on_ask=_follow_up)


def _cancel(confirmation) -> None:
    """取消这笔写操作：只清 agent 层的会话确认态 —— 不调工具、不执行（铁律 3）。"""
    confirm_card.clear(confirmation.session_id)
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


def _render_confirm() -> None:
    """确认卡 / OTP 卡：状态取自 agent 层，动作回灌编排层。"""
    confirmation = confirm_card.current(st.session_state["session_id"])
    if confirmation is None:
        return
    if confirmation.stage == "otp":
        action = ui.otp_card(confirmation)
        if action is not None:
            if action[0] == "cancel":
                _cancel(confirmation)
            elif action[0] == "submit":
                _ask(action[1], display="（短信验证码已提交，值不回显）")
            st.rerun()
        return
    action = ui.confirm_card(confirmation)
    if action == "confirm":
        _ask("确认")
        st.rerun()
    if action == "cancel":
        _cancel(confirmation)
        st.rerun()


# ---------------- 表单桥（卡 20 收款人 / F10 风险测评） ----------------
# 两个表单的渲染与提交都拆到 `interfaces/web/forms.py`（F10 接通后本文件超红线 300）；
# 这里只负责"交给它、按结果 rerun"，会话状态仍由本模块唯一持有。

def _render_forms() -> None:
    """渲染全部表单桥；任一表单提交了 → rerun（与历史 `_render_payee_form` 的行为一致）。"""
    if forms.render_all(st.session_state, _agent_executor()):
        st.rerun()


# ---------------- 侧边栏 ----------------

def _sidebar() -> None:
    """侧边栏：账户余额 + 快捷场景 + 会话信息（页内动作全部回到编排层）。"""
    with st.sidebar:
        st.markdown('<div class="bank-brand" role="heading" aria-level="1" aria-label="AI Banking Agent"><span aria-hidden="true">AI Banking</span><span aria-hidden="true">Agent</span></div>', unsafe_allow_html=True)
        st.divider()
        st.markdown("#### 账户余额")
        if ui.balance_box(st.session_state["balance"]):
            _ask(ui.BALANCE_PROMPT)
            st.rerun()
        st.divider()
        st.markdown("#### 快捷场景")
        if (prompt := ui.scenes()) is not None:
            if prompt == '查看操作记录':
                st.session_state['operation-records-open'] = True
            else:
                _ask(prompt)
        st.divider()
        if st.button("🎬 一键演示（五连问 · 供录屏）", key="demo-run", width="stretch"):
            from agent import scene_flow
            _agent_executor().submit(scene_flow.clear, st.session_state["session_id"]).result()
            st.session_state["demo-queue"] = list(DEMO_SCRIPT)
            st.session_state["messages"] = [{"role": "assistant", "text": GREETING, "turn": None}]
            st.session_state['bound-card-query'] = False
            st.rerun()
        if st.button("重置会话", icon=":material/restart_alt:", key="reset-session", width="stretch"):
            from agent import scene_flow
            _agent_executor().submit(scene_flow.clear, st.session_state["session_id"]).result()
            confirm_card.reset_state()
            cards_ui.reset()
            limits_ui.reset()
            locks_ui.reset()
            lost_ui.reset()
            st.session_state["bound-card-query"] = False
            st.session_state['operation-records-open'] = False
            for key, value in (("turns", []), ("balance", []), ("pending_id", None), ("clarify_round", 0)):
                st.session_state[key] = value
            st.session_state["messages"] = [{"role": "assistant", "text": GREETING, "turn": None}]
            st.rerun()


def _chat_page() -> None:
    queue = list(st.session_state.get("demo-queue") or ())
    if queue:
        st.session_state["demo-queue"] = queue[1:]
        _ask(queue[0])
        st.rerun()
    messages = st.session_state["messages"]
    audit_ui.render(_agent_executor())                            # 侧边栏：审计回放
    ui.transcript(messages, payee_card=payee_ui.render_payee_card, insight_card=_insight_card)
    import scene_ui
    scene_ui.render(_agent_executor(), st.session_state["session_id"], _ask)
    _bill_charts()
    activity_ui.render(st.session_state['turns'])
    _render_pending()
    payee_picker_ui.render(_agent_executor(), st.session_state["session_id"])  # 解析卡（同名多命中）
    card_edit_ui.render(_agent_executor(), st.session_state["session_id"])   # S5：可编辑卡片面板
    #: `_render_forms()` 内部即 `render_payee_form() or render_risk_form()`（`forms.render_all`），
    #: 已覆盖 main 上的 `_render_payee_form()`，故不再单独调用（重复调用会让表单渲染两次）。
    _render_forms()
    if st.session_state.get("bound-card-query"):
        cards_ui.render(_agent_executor(), st.session_state["session_id"], show_limit_button=False)
    else:
        limits_ui.render(_agent_executor(), st.session_state["session_id"], show_button=False)
        locks_ui.render(_agent_executor(), st.session_state["session_id"], show_button=False)
    if st.session_state.get("bound-lost-query"):
        lost_ui.render(_agent_executor(), st.session_state['session_id'], show_button=False)
    if st.session_state.get("bound-payee-query"):                 # S6：收款人卡（人名选项 → 专属详情卡）
        payee_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-schedule-query"):              # F16：预约卡面 + 执行/改时间/取消
        schedule_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-aa-query"):                    # F22：AA 卡面 + 登记已付/撤销
        aa_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-gift-query"):                  # F20：送礼查询卡 + 支付/取消
        gift_card_ui.render(_agent_executor(), st.session_state["session_id"])
    #: 2026-10-24 修：确认卡放在**所有卡片之后**（聊天框最下方）——
    #: 之前在收款人卡上方，用户点「改备注」后卡出现在上面，像是"上一条消息的回执"。
    _render_confirm()
    if (text := st.chat_input("")) and text.strip():
        _ask(text)
        st.rerun()


def _bill_charts() -> None:
    latest = ui.latest_bill(st.session_state['messages'])
    if not latest:
        return
    with st.expander('📊 这份账单的图表', expanded=True):
        _bill_contents(latest)


def _bill_contents(latest: dict) -> None:
    st.caption('图表来自聊天中的账单查询结果。')
    messages = st.session_state["messages"]
    series = ui.bill_series(messages)
    st.button(f'⬇️ 拉取近 {len(PULL_PERIODS)} 期账单（逐期走编排层）',
              type='primary', on_click=_pull_bills)
    ui.bill_panel(series, latest)


def _pull_bills() -> None:
    for period in PULL_PERIODS:
        _ask(f'出一份{period}的账单报告')


def main() -> None:
    st.set_page_config(page_title="AI Banking Agent · 模拟环境", page_icon="🏦", layout="wide")
    st.markdown(ui.load_css(), unsafe_allow_html=True)
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
