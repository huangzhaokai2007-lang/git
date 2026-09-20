"""卡 16：交互层聊天界面（Streamlit）—— 只做「搬运 + 展示」，业务一律在 agent/ 层。

分层（CLAUDE.md 架构 + 卡 16「禁止在界面里写业务逻辑」）：
- 本层**只** `import agent.orchestrator` / `agent.confirm_card`（+ 同目录展示组件）；
  **不 import `tools/`、不 import `data/`、不写 SQL、不算金额、不判权限档**；
- 一句话交给 `orchestrator.handle(...)`，拿回 `Turn`（回执 + 状态轨迹 + 档位 + trace_id）后原样展示；
- 确认卡的「确认 / 取消 / 提交验证码」都走编排层（铁律 3：preview → 定档 → 确认 → 幂等执行）；
- 合成库不存在时**只调用 data/ 层的 CLI**（`python -m data.seed` 子进程），既不 import 也不写 SQL。

跑法：`uv run streamlit run interfaces/web/app.py`（在仓库根目录）
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
os.environ.setdefault("DB_PATH", str(ROOT / "data" / "bank.db"))   # 只设环境变量，不 import data/

from dotenv import load_dotenv                                  # noqa: E402  （.env 定位与 cwd 无关）
from agent import confirm_card, orchestrator                     # noqa: E402

import components as ui                                         # noqa: E402

load_dotenv(ROOT / ".env")

PAGES = ("💬 聊天", "📊 账单图表", "🧾 审计时间轴")
PULL_PERIODS = ("本月", "上个月", "上上个月")
GREETING = ("您好，我是**模拟银行**智能体（全部为合成数据）。试试：「上个月花了多少」「给王五转 100 元」"
            "「我有哪些订阅」。转账会先出确认卡，确认后才执行；也可以点左侧的快捷场景。")

#: 不把上一轮用户话当 history 送给分类器：`classifier.build_messages` 会把 history 与当前话拼进
#: **同一条 user 消息**，实测「带历史」时连续 6 句全部被上一轮意图带偏（如「查一下我的储蓄卡余额」
#: 被上一轮账单请求带成 bill_report）；不带历史 6/6 正确。跨轮上下文由确认/OTP 的在途流程承载。
#: 这是 agent/ 层的历史口径问题，见交付说明「待拍板」。
HISTORY = None


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
    state.setdefault("session_id", f"web-{uuid.uuid4().hex[:8]}")
    state.setdefault("messages", [{"role": "assistant", "text": GREETING, "turn": None}])
    state.setdefault("turns", [])
    state.setdefault("balance", [])
    state.setdefault("pending_id", None)
    state.setdefault("clarify_round", 0)


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
    state["messages"].append({"role": "user", "text": display or text})
    try:
        turn = _agent_executor().submit(orchestrator.handle, text, history=HISTORY,
                                        clarify_round=state["clarify_round"],
                                        session_id=state["session_id"]).result()
    except Exception as exc:                    # noqa: BLE001 —— 不吞异常：如实展示并给处置建议
        state["messages"].append({"role": "assistant", "turn": None,
                                  "text": f"这次请求没能完成（{type(exc).__name__}: {exc}）。"
                                          "如果这是库结构问题（例如旧库缺表），在仓库根目录跑 "
                                          "`uv run python -m data.seed --reset` 重新生成合成数据后再试。"})
        return
    dump = turn.model_dump()
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display or text, "turn": dump})
    state["clarify_round"] = state["clarify_round"] + 1 if "CLARIFY" in turn.states else 0
    state["pending_id"] = turn.pending_id
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


# ---------------- 卡 20：收款人自助添加（聊天触发 → 表单 → 经 agent 落库） ----------------

def _render_payee_form() -> None:
    """编排层判出 `payee_add` 时弹表单；提交**经 agent 层**落库。

    界面只判断"该不该弹"（看 `turn.intent`）并把两个输入原样交出去 —— 脱敏、校验、落库、审计
    全在 `agent/payee_flow.py` → `tools/payee.py`（界面不写业务逻辑）。
    """
    turn = st.session_state["messages"][-1].get("turn") or {}
    if turn.get("intent") != "payee_add" or turn.get("executed"):
        return                       # 提交成功（executed=True）就把表单收起来，避免重复提交；失败则保留可重试
    submitted = ui.payee_form()
    if submitted is None:
        return
    _submit_payee(*submitted)
    st.rerun()


def _submit_payee(name: str, phone: str) -> None:
    """表单提交：走 `orchestrator.submit_payee`（agent 层入口），不直连 `tools/`。

    手机号**只经手不记录**：聊天记录只写"（表单提交）"，脱敏后的号码由回执（agent 层）给出。
    记账口径与 `_ask` 相同（刻意不复用：`_ask` 走的是"一句话"入口，签名与副作用都不同）。
    """
    state = st.session_state
    state["messages"].append({"role": "user", "text": "（表单提交）新增收款人"})
    try:
        turn = _agent_executor().submit(orchestrator.submit_payee, name, phone,
                                        session_id=state["session_id"]).result()
    except Exception as exc:                    # noqa: BLE001 —— 不吞异常：如实展示并给处置建议
        state["messages"].append({"role": "assistant", "turn": None,
                                  "text": f"这次提交没能完成（{type(exc).__name__}: {exc}）。"
                                          "如果是库结构问题，在仓库根目录跑 "
                                          "`uv run python -m data.seed --reset` 后重试。"})
        return
    dump = turn.model_dump()
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": "（表单提交）新增收款人", "turn": dump})
    state["clarify_round"] = 0                  # 表单提交不是追问的后续


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
            _ask(prompt)
            st.rerun()
        st.divider()
        st.caption(f"会话 `{st.session_state['session_id']}` · 已处理 {len(st.session_state['turns'])} 次请求")
        if st.button("🧹 重置会话态", key="reset-session", width="stretch"):
            confirm_card.reset_state()
            for key, value in (("turns", []), ("balance", []), ("pending_id", None), ("clarify_round", 0)):
                st.session_state[key] = value
            st.session_state["messages"] = [{"role": "assistant", "text": GREETING, "turn": None}]
            st.rerun()
    return page


# ---------------- 三个页面 ----------------

def _chat_page() -> None:
    messages = st.session_state["messages"]
    ui.transcript(messages)
    latest = ui.latest_bill(messages)
    if latest.get("categories"):
        with st.expander("📊 这份账单的图表", expanded=True):
            ui.bill_panel(ui.bill_series(messages), latest)
    _render_pending()
    _render_confirm()
    _render_payee_form()
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
    ui.sim_banner()
    page = _sidebar()
    if page == PAGES[0]:
        _chat_page()
    elif page == PAGES[1]:
        _bill_page()
    else:
        _audit_page()


main()
