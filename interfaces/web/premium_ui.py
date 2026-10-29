"""「高级感」UI 组件（纯展示层，**不含任何业务判定**）。

三件事：
1. `pipeline()`：把本轮状态机的路径摊开 —— 让「等」变成「看得见的推理」；
2. `evidence()`：确认卡下的「依据」折叠面板 —— 为什么是这一档，逐条列限额/额度/因子；
3. `examples()` / `toast_for()` / `inject_css()`：示例胶囊、成功微反馈、卡片入场动效。

数据来源一律是 `Turn` 与 `Confirmation.evidence`（由工具层事实包带入），界面不派生数字。
"""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

#: 状态 → (图标, 中文)。没列到的状态原样显示，不猜。
STATE_CN = {
    "IDLE": ("💤", "待命"),
    "CLASSIFY": ("🧠", "理解意图"),
    "SLOT_FILL": ("🎯", "解析收款人 / 补槽"),
    "CLARIFY": ("❓", "追问"),
    "PRECHECK": ("🛡", "核对额度与档位"),
    "CONFIRM_CARD": ("🧾", "生成确认卡"),
    "PENDING_REVIEW": ("🕓", "转人工复核"),
    "EXECUTE": ("⚙", "执行"),
    "VERIFY_NUMBERS": ("🔍", "数字校验"),
    "REPLY": ("💬", "回执"),
    "AUDIT": ("📝", "写审计"),
    "REFUSE": ("🚫", "拒绝"),
}

#: 事实包键 → 人话（依据面板用；只列有意义的几个）
EVIDENCE_LABELS = (
    ("limits_single_max_yuan", "单笔转账上限"),
    ("limits_daily_remaining_yuan", "今日剩余额度"),
    ("whitelist_quota", "白名单专属额度（分）"),
    ("quota_remaining_yuan", "白名单额度剩余"),
    ("remaining_quota_yuan", "白名单额度剩余"),
    ("amount_yuan", "本笔金额"),
    ("tier", "结论档位"),
)

def _css() -> str:
    """动效层由 premium_css 提供（拆文件是为了两边都在 300 行内、职责也更清楚）。"""
    import premium_css
    return premium_css.CSS


_TONE = {"ok": "dsh-tone-ok", "warn": "dsh-tone-warn", "danger": "dsh-tone-danger",
         "draft": "dsh-tone-draft"}


def inject_css() -> None:
    """注入一次（幂等：Streamlit 每次 rerun 会重渲染，重复注入无害）。"""
    st.markdown(_css(), unsafe_allow_html=True)


def card_head(title: str, subtitle: str = "", tone: str = "") -> None:
    """统一的卡头：左色条 + 标题 + 副标题（收款人卡/确认卡共用一套观感）。"""
    cls = _TONE.get(tone, "")
    sub = f'<div class="s">{subtitle}</div>' if subtitle else ""
    st.markdown(f'<div class="dsh-head {cls}"><div class="t">{title}</div>{sub}</div>',
                unsafe_allow_html=True)


def pipeline_text(turn: Any) -> str:
    """管线条的**纯文本**部分（不含 HTML），空串表示不该显示。

    只取 `Turn.states` 里**真实经过**的状态（跳过 IDLE 这类噪声），再加意图与档位 ——
    这行同时是「五层架构在跑」的现场证据。拆成纯函数是为了可单测（渲染层不做逻辑）。
    """
    if turn is None:
        return ""
    states = [s for s in (getattr(turn, "states", None) or []) if s in STATE_CN and s != "IDLE"]
    if not states:
        return ""
    parts = [f"{STATE_CN[s][0]} {STATE_CN[s][1]}" for s in states[-6:]]
    head = f"{getattr(turn, 'intent', '') or '—'}"
    if getattr(turn, "tier", None):
        head += f" · 档位 {turn.tier}"
    return f"{head} ｜ {' → '.join(parts)}"


def pipeline(turn: Any) -> None:
    """把本轮状态机的路径摊成一行（内容由 `pipeline_text` 决定）。

    每一步包一个 `<span class="dsh-step">` 并递增延迟 —— 视觉上"一步步想过去"，
    比一整行同时出现更像在推理（纯 CSS 动画，不改变任何数据）。
    """
    text = pipeline_text(turn)
    if not text:
        return
    head, _, rest = text.partition(" ｜ ")
    steps = [step.strip() for step in rest.split(" → ") if step.strip()]
    spans = "".join(
        f'<span class="dsh-step" style="animation-delay:{index * 70}ms">'
        f'{" → " if index else ""}{step}</span>'
        for index, step in enumerate(steps))
    st.markdown(f'<div class="dsh-pipe"><b>{head}</b> ｜ {spans}</div>',
                unsafe_allow_html=True)


def evidence_rows(confirmation: Any) -> list[tuple[str, str]]:
    """「依据」面板要显示的 `(标签, 值)` 列表（纯函数，可单测；空列表表示不显示）。"""
    facts: Mapping = getattr(confirmation, "evidence", None) or {}
    rows: list[tuple[str, str]] = []
    for key, label in EVIDENCE_LABELS:
        if key in facts and facts[key] not in (None, ""):
            value = str(facts[key])
            if key == "limits_single_max_yuan" and facts.get("single_max_exempt"):
                #: 白名单额度内不受单笔上限约束（额度即该收款人的天花板）——别让用户以为被限额卡住
                value = f"{value}（白名单额度内不适用）"
            rows.append((label, value))
    factors = facts.get("factors")
    if isinstance(factors, (list, tuple)) and factors:
        rows.append(("命中因子", "、".join(str(f) for f in factors)))
    return rows


def evidence(confirmation: Any) -> None:
    """确认卡下的「依据」折叠面板：为什么是这一档，逐条摊开（纯展示）。"""
    rows = evidence_rows(confirmation)
    if not rows:
        return
    with st.expander("▸ 依据：为什么是这个档位", expanded=False):
        st.caption("以下数字全部来自工具层事实包（不是模型生成的）。")
        for label, value in rows:
            st.markdown(f"- {label}：`{value}`")
        st.caption(f"判定：{'需要短信验证码' if getattr(confirmation, 'requires_otp', False) else '免验证码'}"
                   f"（档位 {getattr(confirmation, 'tier', '—')}）")


def toast_for(turn: Any) -> None:
    """执行结果微反馈（成功/失败各一句）；只读回合不打扰。"""
    if turn is None:
        return
    if getattr(turn, "executed", False) and not getattr(turn, "error_code", None):
        st.toast("操作已完成", icon="✅")
    elif getattr(turn, "error_code", None):
        st.toast("这次没办成，看下面的说明", icon="⚠️")


EXAMPLES = ("查一下余额", "给房东转 100 元", "向王五和赵六 群收款 300 元", "我的收款人有哪些")


def examples() -> str | None:
    """**还没开始对话**时的示例胶囊：点一下就等于说那句话（新手 3 秒上手，录屏好看）。

    2026-10-27 修：原来判的是 `messages` 是否为空，但首页预置了一句问候语 → 永远非空，
    胶囊**从未出现过**（实测）。改成看 `turns`：只要还没真的问过话就展示。
    """
    if st.session_state.get("turns"):
        return None
    st.caption("可以这样说（点一下试试）：")
    picked = st.pills("示例指令", EXAMPLES, key="premium-examples", label_visibility="collapsed")
    return picked or None


def trace_footer(turn: Any) -> None:
    """回执下的「可追溯」页脚：trace_id 可复制，指向侧边栏的审计回放。

    **为什么值得有**：金融场景里「这句话到底改了什么」必须查得到。
    把 trace_id 摆在回执下方（而不是藏在日志里），用户/评委可以直接拿去回放整条链路。
    """
    trace = getattr(turn, "trace_id", None)
    if not trace:
        return
    with st.expander("🔎 这一笔可追溯", expanded=False):
        st.caption("复制这条编号，粘贴到侧边栏「审计回放」即可看到完整链路"
                   "（意图 → 解析 → 档位 → 工具入参 → 结果）。")
        st.code(trace, language="text")
        st.caption(trace_bits(turn))


def trace_bits(turn: Any) -> str:
    """「可追溯」页脚的一句话摘要（纯函数，可单测）。"""
    bits = [f"意图 {getattr(turn, 'intent', '—') or '—'}"]
    if getattr(turn, "tier", None):
        bits.append(f"档位 {turn.tier}")
    bits.append("已执行" if getattr(turn, "executed", False) else "未执行")
    if getattr(turn, "error_code", None):
        bits.append(f"错误码 {turn.error_code}")
    return " · ".join(bits)
