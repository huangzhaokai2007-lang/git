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

CSS = """
<style>
/* 卡片入场：新出现的卡片轻微上浮 + 淡入（180ms，金融产品里"顺手"的来源） */
@keyframes dsh-rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
div[data-testid="stVerticalBlockBorderWrapper"] { animation: dsh-rise .18s ease-out both; }
/* 金额一律等宽数字，纵向对齐（避免数字跳动时宽度抖动） */
.dsh-num, table, [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
/* 管线条：一行紧凑的「步骤 → 步骤」 */
.dsh-pipe { font-size: .82rem; color: #9aa0ac; margin: .1rem 0 .4rem 0; letter-spacing: .2px; }
.dsh-pipe b { color: #d1d4dc; font-weight: 600; }
.dsh-pipe .dim { opacity: .55; }
/* 卡头：左侧色条标语义（成功/等待/危险/草稿） */
.dsh-head { border-left: 3px solid #6667C9; padding: .1rem 0 .1rem .6rem; margin-bottom: .35rem; }
.dsh-head .t { font-size: 1.02rem; font-weight: 650; color: #E6E8EE; }
.dsh-head .s { font-size: .78rem; color: #9aa0ac; margin-top: .1rem; }
.dsh-tone-ok { border-left-color: #22C55E; }
.dsh-tone-warn { border-left-color: #F59E0B; }
.dsh-tone-danger { border-left-color: #EF4444; }
.dsh-tone-draft { border-left-color: #6B7280; }
</style>
"""

_TONE = {"ok": "dsh-tone-ok", "warn": "dsh-tone-warn", "danger": "dsh-tone-danger",
         "draft": "dsh-tone-draft"}


def inject_css() -> None:
    """注入一次（幂等：Streamlit 每次 rerun 会重渲染，重复注入无害）。"""
    st.markdown(CSS, unsafe_allow_html=True)


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
    """把本轮状态机的路径摊成一行（内容由 `pipeline_text` 决定）。"""
    text = pipeline_text(turn)
    if not text:
        return
    head, _, rest = text.partition(" ｜ ")
    st.markdown(f'<div class="dsh-pipe"><b>{head}</b> ｜ {rest}</div>', unsafe_allow_html=True)


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


EXAMPLES = ("查一下余额", "给房东转 100 元", "向王五和赵六 AA 收款 300 元", "我的收款人有哪些")


def examples() -> str | None:
    """空会话时的示例胶囊：点一下就等于说那句话（新手 3 秒上手，录屏好看）。"""
    if st.session_state.get("messages"):
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


def presets_panel(presets: dict) -> None:
    """侧边栏「演示预设」：把该用什么输入摆出来（卡号/密码/收款人/限额/OTP）。

    为什么放界面里：预置凭证只写在代码里，演示时要翻文件；重置库后凭证表甚至还不存在
    （首次绑卡才 provision）。把这些**公开的合成夹具**摆到界面上，评委/朋友自己就能试。
    """
    if not presets:
        return
    with st.sidebar:
        with st.expander("🔑 演示预设（卡号 / 密码 / 收款人）", expanded=False):
            facts = presets.get("facts") or {}
            st.markdown("**可用银行卡**（首次绑卡时用它）")
            #: ⚠️ 不变量（队友用例钉住）：**完整卡号只能在输入正确密码后出现**。
            #: 所以这里默认只给脱敏卡号；要点按钮才显示，且用 `st.code`（不是 markdown 正文）。
            reveal = st.toggle("显示完整卡号（演示用）", key="presets-reveal")
            for card in presets.get("cards") or []:
                number = str(card["card_no"])
                masked = f"{number[:4]} **** **** {number[-4:]}"
                if reveal:
                    st.code(number, language="text")
                else:
                    st.markdown(f"- `{masked}`")
                st.caption(f"　密码 `{card['password']}`")
            st.markdown("**收款人**")
            for person in presets.get("payees") or []:
                quota = f" · 额度 {person['quota_yuan']:,.0f} 元/月" if person.get("quota_yuan") else ""
                expire = f" · 至 {person['expire']}" if person.get("kind") == "白名单" else ""
                st.markdown(f"- {person['name']}（{person['phone']}）· {person['kind']}{quota}{expire}")
            st.caption(f"单笔上限 {facts.get('limits_single_max_yuan', '—')} 元 · "
                       f"日累计 {facts.get('limits_daily_max_yuan', '—')} 元 · "
                       f"夜间 {facts.get('night_from_hour', '—')}:00–{facts.get('night_to_hour', '—')}:00 · "
                       f"短信验证码固定 `{facts.get('otp', '—')}`")
