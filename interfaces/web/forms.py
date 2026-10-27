"""交互层**表单桥**（从 `interfaces/web/app.py` 拆出）：聊天触发 → 弹表单 → 经 agent 落库/计分。

为什么单开一个文件：`app.py` 在 F10 加上风险测评问卷后到 328 行（红线 300，CLAUDE.md 第 51 行）。
两个表单（卡 20 的收款人、F10 的问卷）是同一类东西 —— 「编排层判出某个意图 → 界面弹对应表单 →
提交时经 `agent/` 回灌」，边界清晰，故整块拆出。`app.py` 只需在 `_chat_page` 里调 `forms.render_all(state)`。

分层（与 `app.py` 同口径，`tests/test_web_layering.py` 钉着）：本模块属交互层，
**只 import `agent/`**（+ 同目录展示组件），不 import `tools/` / `data/` / `guard/`、不写 SQL、不算业务数字。
"""

from __future__ import annotations

import time
from typing import Any, Mapping

import streamlit as st

from agent import orchestrator, payee_flow, risk_flow

import components as ui


def risk_form(questions: Mapping[str, tuple[str, tuple[str, ...]]]) -> dict[str, int] | None:
    """F10：风险测评问卷（5 题一次收齐）。

    `questions` 由调用方从 `agent.risk_flow.QUESTIONS` 传入 —— **题干与选项文案的唯一来源在
    agent 层**，界面不复制一份（复制就会漂移）。本函数只**收集**答案并原样返回；
    计分 / 校验 / 未成年拦截一律在 `tools/` → `agent/`（界面不写业务逻辑）。

    四道选择题用 `selectbox`（选项顺序 = 分值的唯一定义，见 `tools/_wealth_risk.QUESTION_SCORE_RULE`）；
    年龄题用 `number_input`（收的是**岁数**，不是选项下标 —— 工具层 `AGE_BANDS` 按岁数分段）。
    未提交 → `None`；提交 → `{题号: 分值或岁数}`。
    """
    answers: dict[str, int] = {}
    with st.container(border=True):
        st.markdown(f"##### 📋 风险承受能力测评（{len(questions)} 题）")
        st.caption("全部由代码计分，结果仅保存在本次会话中。未满 18 岁无法测评。")
        with st.form("risk-form", clear_on_submit=False):
            for key, (title, options) in questions.items():
                if key == "age":
                    answers[key] = int(st.number_input(title, min_value=0, max_value=120,
                                                       value=30, step=1, key=f"risk-{key}"))
                else:
                    picked = st.selectbox(title, options, key=f"risk-{key}",
                                          index=min(2, len(options) - 1))
                    answers[key] = options.index(picked)      # 选项下标 = 分值下标（工具层口径）
            submitted = st.form_submit_button("提交测评", type="primary", width="stretch")
        if submitted:
            return answers
    return None


def _ask_submit(state: Mapping[str, Any], text: str) -> None:
    """把一次表单提交记进聊天记录（**不执行**任何业务；执行在各自 `_submit_*` 里）。

    `state` 由调用方传入（`app.py` 的 `st.session_state`）—— 本模块不自己抓全局态，
    便于 `app.py` 继续做"唯一持有会话状态的人"。
    """
    state["messages"].append({"role": "user", "text": text})


def _record(state: Mapping[str, Any], turn: Any, display: str) -> None:
    """记下 agent 层返回的一次 `Turn`（回执 + 轨迹），与 `app._ask` 同口径。"""
    dump = turn.model_dump()
    import workspace_ui
    workspace_ui.accept(dump, display)
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": dump})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display, "turn": dump})


def _failure(state: Mapping[str, Any], exc: Exception) -> None:
    """异常如实展示并给处置建议（不吞异常 —— 与 `app._ask` 同口径）。"""
    state["messages"].append({"role": "assistant", "turn": None,
                              "text": f"这次提交没能完成（{type(exc).__name__}: {exc}）。"
                                      "如果是库结构问题，在仓库根目录跑 "
                                      "`uv run python -m data.seed --reset` 后重试。"})


# ---------------- 卡 20：收款人自助添加 ----------------

def render_payee_form(state: Mapping[str, Any], executor: Any) -> bool:
    """编排层判出 `payee_add` 时弹表单；提交**经 agent 层**落库。返回是否已处理（调用方据此 rerun）。

    界面只判断"该不该弹"（看 `turn.intent`）并把两个输入原样交出去 —— 脱敏、校验、落库、审计
    全在 `agent/payee_flow.py` → `tools/payee.py`（界面不写业务逻辑）。
    """
    turn = state["messages"][-1].get("turn") or {}
    if turn.get("intent") != "payee_add" or turn.get("executed"):
        return False                  # 提交成功（executed=True）就把表单收起来；失败则保留可重试
    defaults = executor.submit(payee_flow.pending_slots, state["session_id"]).result()
    submitted = ui.payee_form(defaults)
    if submitted is None:
        return False
    payee_flow.clear_pending(state["session_id"])
    _ask_submit(state, "（表单提交）新增收款人")
    try:
        result = executor.submit(
            orchestrator.submit_payee, submitted["name"], submitted["phone"],
            session_id=state["session_id"], whitelist=submitted["whitelist"],
            note=submitted["note"], quota=submitted["quota"],
            expire=submitted["expire"], tags=submitted.get("tags", ""),
            card_no=submitted.get("card_no", "")).result()
    except Exception as exc:                    # noqa: BLE001 —— 不吞异常：如实展示并给处置建议
        _failure(state, exc)
        return True
    _record(state, result, "（表单提交）新增收款人")
    return True


# ---------------- F10：风险测评问卷 ----------------

def render_risk_form(state: Mapping[str, Any], executor: Any) -> bool:
    """F10：编排层判出 `risk_assess` 时弹问卷；提交**经 agent 层**计分。返回是否已处理。

    界面只判断"该不该弹"（看 `turn.intent`）并把 5 个输入原样交出去 —— 计分、校验、未成年拦截
    全在 `agent/risk_flow.py` → `tools/_wealth_risk.py`（界面不写业务逻辑，也不做任何算术）。
    题干文案取自 `risk_flow.QUESTIONS`（单一来源），界面不复制。
    """
    turn = state["messages"][-1].get("turn") or {}
    if turn.get("intent") != "risk_assess" or turn.get("executed"):
        return False                  # 计分成功就把问卷收起来；失败则保留可重试
    answers = risk_form(risk_flow.QUESTIONS)
    if answers is None:
        return False
    _ask_submit(state, "（问卷提交）风险测评")
    try:
        result = executor.submit(orchestrator.submit_assessment, answers,
                                 session_id=state["session_id"]).result()
    except Exception as exc:                    # noqa: BLE001 —— 不吞异常：如实展示并给处置建议
        _failure(state, exc)
        return True
    _record(state, result, "（问卷提交）风险测评")
    return True


def render_all(state: Mapping[str, Any], executor: Any) -> bool:
    """渲染全部表单桥（任一表单提交了 → 返回 True，调用方据此 rerun）。"""
    return render_payee_form(state, executor) or render_risk_form(state, executor)
