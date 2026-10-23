"""卡 16：交互层聊天界面（Streamlit），业务一律在 agent/ 层。
分层（CLAUDE.md 架构）：
- 本层**只** `import agent.orchestrator` / `agent.confirm_card`（+ 同目录展示组件）；
  **不 import `tools/`、不 import `data/`、不写 SQL、不算金额、不判权限档**；
- 一句话交给 `orchestrator.handle(...)`，拿回 `Turn`（回执 + 状态轨迹 + 档位 + trace_id）后原样展示；
- 合成库不存在时**只调用 data/ 层的 CLI**（`python -m data.seed` 子进程），既不 import 也不写 SQL。

跑法：`uv run streamlit run interfaces/web/app.py`
"""

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
import payee_card_ui                                             # noqa: E402  （S6：收款人卡）
import forms                                                     # noqa: E402  （F10：表单桥拆出，含风险测评问卷）
import payee_card_ui as payee_ui                                 # noqa: E402  （T20：查看白名单的卡片渲染）
import card_edit_ui                                             # noqa: E402  （S5：可编辑卡片）
import payee_picker_ui                                          # noqa: E402  （解析卡：同名多命中）
import schedule_card_ui                                         # noqa: E402  （预约卡面：执行/改时间/取消）
import aa_card_ui                                               # noqa: E402  （AA 卡面：登记已付/撤销）
import gift_card_ui                                             # noqa: E402  （送礼查询卡：支付/取消）

load_dotenv(ROOT / ".env")

PAGES = ("💬 聊天", "📊 账单图表", "🧾 审计时间轴", "💳 我的银行卡")
PULL_PERIODS = ("本月", "上个月", "上上个月")
GREETING = ("您好，我是**模拟银行**智能体（全部为合成数据）。试试：「上个月花了多少」「给王五转 100 元」"
            "「我有哪些订阅」。转账会先出确认卡，确认后才执行；也可以点左侧的快捷场景。")

#: 追问续接要带多少条历史（用户话与助手回执交替）。**追问的上下文必须让分类器看到** ——
#: 例如「列出两个李四」那一句，否则用户只回一个「1001」时会被判成 smalltalk。
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
    state["page"] = state.pop("navigate-page", state.get("page", PAGES[0]))
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
    """编排层调用统一跑在这条**常驻线程**上（进程级单例）。

    为什么必须：Streamlit 每次 rerun 换一条线程，而 `data/` 的 SQLite 连接是进程级单例
    （sqlite3 默认校验「同线程才能用」）→ 不同 rerun 直接复用会在第二次交互就报
    `SQLite objects created in a thread can only be used in that same thread`。
    界面层能做的正确处置就是**把 agent 调用串行到一条固定线程**（不越层去改 data/ 的连接策略）；
    更彻底的修法（`check_same_thread=False` 或按线程建连接）属于 data/ 层，见交付说明「待拍板」。
    """
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")

# ---------------- 与编排层的唯一入口 ----------------
def _ask(text: str, *, display: str | None = None) -> None:
    """把一句话交给编排层，记录回执与轨迹（界面不解释业务、不派生数字）。

    `display`：界面上显示的措辞（验证码这类凭证按 `display` 脱敏，值不回显 —— 规格 §5 要求
    验证码不进日志/回执；本页的轨迹与聊天记录同样不回显它的值）。
    """
    state = st.session_state
    display = display or _agent_executor().submit(card_binding_flow.safe_chat_text, text).result()
    state["messages"].append({"role": "user", "text": display or text})
    try:
        #: 追问续接的上下文：把最近几轮**真实对话**（用户话 + 助手回执）交给分类器。
        #: **修的问题**：此前传的是模块常量 `HISTORY`，而它**恒为 `None`**（全项目没有第二处赋值）。
        #: 于是「给李四转100元 → 界面列出两个李四 → 用户回 1001」这一步，分类器只看到一个光秃秃的
        #: 数字 → 判成 `smalltalk` → 收款人永远解析不出来（实机复现，用户已报）。实测把那一句
        #: 候选列表作为 history 传进去后，`1001` 能被正确解析成 `payee='李四（139****1001）'`。
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
    if turn.intent == "card_query":
        cards_ui.reset(turn)
    if turn.ask in ("card_lock", "card_missing", "card_unlock"): limits_ui.reset()
    if turn.ask == "limit_adjust": locks_ui.reset()
    limits_ui.accept_turn(_agent_executor(), state["session_id"], text, turn.ask)
    locks_ui.accept_turn(_agent_executor(), state["session_id"], turn.ask)
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display or text, "turn": dump})
    state["clarify_round"] = state["clarify_round"] + 1 if "CLARIFY" in turn.states else 0
    state["pending_id"] = turn.pending_id
    state["bound-card-query"] = turn.intent == "card_query" and turn.error_code is None
    state["bound-payee-query"] = turn.intent == "payee_list" and turn.error_code is None
    state["bound-schedule-query"] = turn.intent == "scheduled_list" and turn.error_code is None
    state["bound-aa-query"] = turn.intent == "aa_list" and turn.error_code is None
    state["bound-gift-query"] = turn.intent == "gift_order_list" and turn.error_code is None
    if turn.intent == "balance_query" and not turn.error_code and turn.reply:
        state["balance"] = [item for item in state["balance"] if item["text"] != turn.reply][-1:]
        state["balance"].append({"text": turn.reply, "trace_id": turn.trace_id})


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

def _sidebar() -> str:
    """侧边栏：账户余额 + 快捷场景 + 会话信息（页内动作全部回到编排层）。"""
    with st.sidebar:
        st.markdown("### 🏦 AI Banking Agent")
        st.caption("2026 FinTechathon · 自建模拟银行核心")
        page = st.radio("页面", PAGES, key="page", label_visibility="collapsed")
        st.divider()
        st.markdown("#### 账户余额")
        if ui.balance_box(st.session_state["balance"]):
            _ask(ui.BALANCE_PROMPT)
            st.rerun()
        st.divider()
        st.markdown("#### 快捷场景")
        if (prompt := ui.scenes()) is not None:
            if prompt in ("卡片查询", "调整额度", "锁卡"):
                st.session_state["navigate-page"] = PAGES[0]
            _ask(prompt)
            st.rerun()
        st.divider()
        st.caption(f"会话 `{st.session_state['session_id']}` · 已处理 {len(st.session_state['turns'])} 次请求")
        if st.button("🧹 重置会话态", key="reset-session", width="stretch"):
            confirm_card.reset_state()
            cards_ui.reset()
            limits_ui.reset()
            locks_ui.reset()
            st.session_state["bound-card-query"] = False
            for key, value in (("turns", []), ("balance", []), ("pending_id", None), ("clarify_round", 0)):
                st.session_state[key] = value
            st.session_state["messages"] = [{"role": "assistant", "text": GREETING, "turn": None}]
            st.rerun()
    return page


def _chat_page() -> None:
    messages = st.session_state["messages"]
    ui.transcript(messages, payee_card=payee_ui.render_payee_card)
    latest = ui.latest_bill(messages)
    if latest.get("categories"):
        with st.expander("📊 这份账单的图表", expanded=True):
            ui.bill_panel(ui.bill_series(messages), latest)
    _render_pending()
    _render_confirm()
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
    if st.session_state.get("bound-payee-query"):                 # S6：收款人卡 + 行内按钮
        payee_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-schedule-query"):              # F16：预约卡面 + 执行/改时间/取消
        schedule_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-aa-query"):                    # F22：AA 卡面 + 登记已付/撤销
        aa_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if st.session_state.get("bound-gift-query"):                  # F20：送礼查询卡 + 支付/取消
        gift_card_ui.render(_agent_executor(), st.session_state["session_id"])
    if (text := st.chat_input("说一句话，例如「上个月花了多少」")) and text.strip():
        _ask(text)
        st.rerun()


def _bill_page() -> None:
    st.markdown("### 📊 账单图表")
    st.caption("图表数据全部解析自**编排层回执**（`bill_report` 的分类表 / `bill_analysis` 的支出合计）。")
    messages = st.session_state["messages"]
    series, latest = ui.bill_series(messages), ui.latest_bill(messages)
    if st.button(f"⬇️ 拉取近 {len(PULL_PERIODS)} 期账单（逐期走编排层）", type="primary"):
        bar = st.progress(0.0, text="正在逐期向编排层取账单…")
        for index, period in enumerate(PULL_PERIODS, start=1):
            _ask(f"出一份{period}的账单报告")
            bar.progress(index / len(PULL_PERIODS), text=f"已取回「{period}」（{index}/{len(PULL_PERIODS)}）")
        st.rerun()
    ui.bill_panel(series, latest)


def _audit_page() -> None:
    st.markdown("### 🧾 审计时间轴")
    ui.timeline(st.session_state["turns"])


def main() -> None:
    st.set_page_config(page_title="AI Banking Agent · 模拟环境", page_icon="🏦", layout="wide")
    st.markdown(ui.CSS, unsafe_allow_html=True)
    if not _ensure_db():
        return
    _init_state()
    cards_ui.prepare(_agent_executor(), st.session_state["session_id"])
    ui.sim_banner()
    page = _sidebar()
    if page == PAGES[0]:
        _chat_page()
    elif page == PAGES[1]:
        _bill_page()
    elif page == PAGES[2]:
        _audit_page()
    else:
        cards_ui.render(_agent_executor(), st.session_state["session_id"])


main()
