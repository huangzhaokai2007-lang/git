"""卡 16：交互层组件（纯展示）—— 只渲染编排层给的东西，**不算任何业务数字**。

本模块的边界（卡 16「禁止在界面里写业务逻辑」+ CLAUDE.md 铁律 1/2）：
- 确认卡的金额、收款人、权限档、是否要 OTP 全部来自 `agent.confirm_card.Confirmation`
  （工具层预览事实包的下游）；界面不判限额、不判权限、不改数字，也不自己比对验证码；
- 图表数据来自**编排层回执文本**（`bill_report` 的 markdown 表 / `bill_analysis` 的一句话）：
  这里只做「回执 → 坐标点」的解析与绘图，不重算、不补数、不猜测；
- 需要更结构化的数据（例如直接拿 `ToolResult.facts`）应当由 agent/ 层开只读入口 —— 见交付说明「待拍板」。
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping, Sequence

import streamlit as st

#: 顶部合规标注（卡 16 第 5 条）
SIM_NOTICE = "模拟环境 · 全部为合成数据：不连接任何真实银行，无真实用户 / 账户 / 资金 / 卡号。"

#: 快捷场景（卡 16 第 1 条）：按钮文案 → 送进编排层的原话。界面只负责「把这句话交给 agent/」。
SCENES: tuple[tuple[str, str], ...] = (
    ("🧾 看账单", "出一份上个月的账单报告"),
    ("💸 转账", "给王五转 100 元"),
    ("🔁 管订阅", "我有哪些订阅"),
    ("💳 管卡", "帮我查一下我的卡"),
)

#: 侧边栏账户余额：同样只是把话交给编排层（界面不读库、不 import data/）。
#: 口径：只说「查一下余额」—— 编排层会取默认账户（储蓄）；一旦提到「储蓄卡/信用卡」，
#: 分类器会填中文槽位（'储蓄卡'/'credit_card'），而工具层的 `account_type` 只认 savings|credit，
#: 编排层没有这层归一化 → 工具直接 INVALID_ARGUMENT。这是 agent/ 层缺口，见交付说明「待拍板」。
BALANCE_PROMPT = "查一下余额"

CSS = """<style>
.sim-banner{background:#fff4e5;border:1px solid #ffd8a8;border-radius:10px;padding:8px 14px;
            color:#8a5a00;font-size:13px;font-weight:700}
ul.tl{list-style:none;margin:0;padding:0 0 0 4px}
ul.tl li{border-left:2px solid #d7dbe0;padding:0 0 10px 16px;margin-left:6px;position:relative}
ul.tl li:last-child{border-left-color:transparent;padding-bottom:0}
ul.tl .dot{position:absolute;left:-7px;top:4px;width:12px;height:12px;border-radius:50%;background:#1a73e8}
ul.tl .k{font-weight:700;color:#202124;font-size:13px}
ul.tl .v{color:#3c4043;font-size:14px;word-break:break-word}
.tl-meta{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#5f6368}
.chip{display:inline-block;background:#e8f0fe;color:#1a73e8;border-radius:999px;
      padding:0 8px;margin-right:4px;font-size:11px}
</style>"""


def _esc(value: object) -> str:
    """HTML 转义（用户输入与回执都可能带尖括号）。"""
    return html.escape("" if value is None else str(value))


def _line(text: str, prefix: str) -> str:
    """从编排层文本里取以 `prefix` 开头的那一行（如确认卡的「风险提示：」）；取不到返回空串。"""
    for line in str(text).splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def sim_banner() -> None:
    st.markdown(f'<div class="sim-banner">{SIM_NOTICE}</div>', unsafe_allow_html=True)


def transcript(messages: Sequence[Mapping[str, Any]]) -> None:
    """聊天记录：用户原话照原样，助手只回显编排层回执 + trace_id。"""
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["text"])
            if (turn := message.get("turn")) is not None:
                st.caption(f"trace_id `{turn['trace_id']}` · 意图 `{turn['intent']}` · "
                           f"档位 `{turn.get('tier') or '—'}` · 状态 {' → '.join(turn['states'])}")


def scenes() -> str | None:
    """快捷场景按钮：返回被点的那句话（没点 → None）。"""
    clicked: str | None = None
    columns = st.columns(2)
    for index, (label, prompt) in enumerate(SCENES):
        if columns[index % 2].button(label, key=f"scene-{index}", width="stretch"):
            clicked = prompt
    return clicked


def balance_box(replies: Sequence[Mapping[str, Any]]) -> bool:
    """侧边栏账户余额：只回显 agent 给过的余额回执（数字来自事实包）。返回是否点了刷新。"""
    if not replies:
        st.caption("还没有余额记录 —— 点下面的按钮让编排层去查（界面不直接读库）。")
    for item in replies:
        st.markdown(f"- {item['text']}")
        st.caption(f"`{item['trace_id']}`")
    return st.button("🔄 刷新余额（走编排层）", key="refresh-balance", width="stretch")


def confirm_card(confirmation: Any) -> str | None:
    """确认卡**独立组件**（卡 16 第 2 条）：金额 / 收款人 / 权限档(风险等级) / 确认 / 取消。

    字段全部来自 `agent.confirm_card.Confirmation`。返回 "confirm" / "cancel" / None。
    """
    with st.container(border=True):
        st.markdown("##### 📋 转账确认卡（等待您的确认）")
        head = st.columns(4)
        head[0].metric("金额（元）", confirmation.amount_yuan)
        head[1].metric("收款人", confirmation.payee_name)
        head[2].metric("权限档（风险等级）", confirmation.tier)
        head[3].metric("短信验证码", "需要" if confirmation.requires_otp else "不需要")
        st.caption(f"意图：{_line(confirmation.card_text, '意图：') or confirmation.intent} · "
                   f"收款账号：{_esc(confirmation.masked_phone)} · "
                   f"预计到账：{_line(confirmation.card_text, '预计到账：') or '—'}")
        if risk := _line(confirmation.card_text, "风险提示："):
            st.warning(f"风险提示：{risk}")
        left, right, _rest = st.columns([1, 1, 3])
        if left.button("✅ 确认", type="primary", key="confirm-ok", width="stretch"):
            return "confirm"
        if right.button("✖ 取消", key="confirm-cancel", width="stretch"):
            return "cancel"
    return None


def otp_card(confirmation: Any) -> tuple[str, str] | None:
    """等 OTP：只收集验证码，交编排层 → 工具层校验（界面不比对、不知晓正确值）。

    用 `st.form`：输入值随提交一起送到后端（裸 `text_input` 的值要按回车才提交，
    演示时点「提交」会拿到空串 → 白白浪费一次尝试）。
    """
    with st.container(border=True):
        st.markdown("##### 🔐 请输入短信验证码")
        st.caption(f"收款人 {_esc(confirmation.payee_name)}（{_esc(confirmation.masked_phone)}）· "
                   f"金额 {_esc(confirmation.amount_yuan)} 元 · 权限档 {_esc(confirmation.tier)}")
        with st.form("otp-form", clear_on_submit=True):
            code = st.text_input("短信验证码", type="password", key="otp-code",
                                 placeholder="请输入 6 位短信验证码", label_visibility="collapsed")
            left, right, _rest = st.columns([1, 1, 3])
            submitted = left.form_submit_button("提交验证码", type="primary", key="otp-submit",
                                               width="stretch")
            cancelled = right.form_submit_button("取消", key="otp-cancel", width="stretch")
        if cancelled:
            return ("cancel", "")
        if submitted:
            return ("submit", code)
    return None


def pending_card(text: str) -> bool:
    """L3 待复核卡：只给撤销入口（撤销本身走 agent/confirm_card，界面不改状态）。"""
    with st.container(border=True):
        st.markdown("##### ⏳ 这笔转账待人工复核（L3）")
        st.markdown(text)
        return st.button("↩️ 撤销这笔转账", key="pending-revoke")


# ---------------- 图表：从编排层回执解析坐标点 ----------------

#: 三种回执形状（全部由 agent/templates 与工具层 markdown 生成，这里只认它们）
_REPORT_HEAD = re.compile(r"(?P<period>\d{4}(?:-\d{2})?) 账单：支出 (?P<out>[\d,]+\.\d{2}) 元")
_ANALYSIS_TOTAL = re.compile(r"(?P<period>\d{4}(?:-\d{2})?) 共支出 (?P<out>[\d,]+\.\d{2}) 元")
_CATEGORY_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<amount>[\d,]+\.\d{2})\s*\|\s*(?P<pct>\d+)%\s*\|$")


def _yuan(text: str) -> float:
    """展示用数值：把回执里的「元」串转成图表坐标（纯格式化，不重算金额）。"""
    return float(str(text).replace(",", ""))


def parse_bill(text: str) -> dict:
    """回执 → 图表数据 `{period, total, categories:[{key, amount, pct}]}`；解析不出返回 `{}`。"""
    parsed: dict[str, Any] = {"period": None, "total": None, "categories": []}
    for line in str(text).splitlines():
        match = _REPORT_HEAD.search(line) or _ANALYSIS_TOTAL.search(line)
        if match is not None:
            parsed["period"], parsed["total"] = match.group("period"), _yuan(match.group("out"))
        elif (row := _CATEGORY_ROW.match(line)) is not None:
            parsed["categories"].append({"key": row.group("key"), "amount": _yuan(row.group("amount")),
                                         "pct": int(row.group("pct"))})
    return parsed if parsed["total"] is not None else {}


def bill_series(messages: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """会话里所有账单回执 → `{账期: 支出合计}`（趋势图每个点都来自一次真实回执）。"""
    series: dict[str, float] = {}
    for message in messages:
        if message["role"] != "assistant":
            continue
        parsed = parse_bill(message["text"])
        if parsed:
            series[str(parsed["period"])] = float(parsed["total"])
    return dict(sorted(series.items()))


def latest_bill(messages: Sequence[Mapping[str, Any]]) -> dict:
    """最后一条可解析的账单回执（分类表来自它）。"""
    for message in reversed(list(messages)):
        if message["role"] == "assistant" and (parsed := parse_bill(message["text"])):
            return parsed
    return {}


def bill_panel(series: Mapping[str, float], latest: Mapping[str, Any]) -> None:
    """账单图表（卡 16 第 3 条）：分类占比 + 月度趋势，只用 streamlit 原生图表。"""
    categories = list(latest.get("categories") or [])
    left, right = st.columns(2)
    with left:
        st.markdown("**支出分类占比**")
        if categories:
            st.bar_chart({item["key"]: item["amount"] for item in categories}, height=280)
            st.caption("分类与金额解析自编排层账单回执（已过数字校验器）。")
        else:
            st.caption("还没有分类明细 —— 让 agent 出一份账单报告（回执里带分类表）。")
    with right:
        st.markdown("**月度趋势（支出合计）**")
        if series:
            st.line_chart(dict(series), height=280)
            st.caption("每个点 = 一次账单回执里的「支出合计」；点「拉取近 3 期」可补齐。")
        else:
            st.caption("还没有账期数据。")
    if categories:
        st.dataframe([{"支出分类": item["key"], "金额（元）": item["amount"], "占比": f"{item['pct']}%"}
                      for item in categories], hide_index=True, width="stretch")


# ---------------- 审计时间轴 ----------------

def timeline(records: Sequence[Mapping[str, Any]]) -> None:
    """审计时间轴（卡 16 第 4 条）：按 trace_id 展开「意图 → 工具 → 权限 → 结果」。"""
    if not records:
        st.info("还没有请求。先到「💬 聊天」页说一句话或点一个快捷场景。")
        return
    st.caption("每次请求编排层都写一条 audit_log（铁律 5）；下面按**同一条请求**的 trace_id "
               "展开编排层轨迹：意图 → 槽位 → 权限 → 工具 → 结果。")
    for record in reversed(records):
        turn = record["turn"]
        with st.expander(f"{record['ts']} · {turn['intent']} · 「{record['text'][:22]}」",
                         expanded=len(records) == 1):
            st.markdown(_steps_html(turn, record["text"]), unsafe_allow_html=True)


def _steps_html(turn: Mapping[str, Any], text: str) -> str:
    """一条请求的时间轴 HTML（每个字段都直接来自 `Turn`，界面不解释）。"""
    steps = [("用户输入", text),
             ("意图识别", f"{turn['intent']}（置信度 {turn['confidence']}）"),
             ("缺槽检查", "、".join(turn["missing_slots"]) or "无"),
             ("权限判定", turn.get("tier") or "未定档（只读恒 L0；拒答与未接通不定档）"),
             ("工具调用", "、".join(turn["tool_calls"]) or "无"),
             ("执行结果", f"executed={turn['executed']} · error_code={turn.get('error_code') or '—'}"),
             ("回执", turn["reply"])]
    items = "".join(f'<li><span class="dot"></span><div class="k">{_esc(key)}</div>'
                    f'<div class="v">{_esc(value)}</div></li>' for key, value in steps)
    chips = "".join(f'<span class="chip">{_esc(state)}</span>' for state in turn["states"])
    return (f'<ul class="tl">{items}</ul>'
            f'<div class="tl-meta">trace_id {_esc(turn["trace_id"])} · 状态轨迹 {chips}</div>')
