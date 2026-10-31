"""「缺槽 → 直接弹卡片填」：把还差的槽位渲染成一张表单卡，提交即继续（不改业务判定）。

用户 2026-10-27 建议：「或许可以直接弹出卡片来填写呢」。
此前缺槽只回一句文字追问，用户得自己按顺序把答案打进来（还容易被当成新意图）。

设计：
- 数据来源 `agent.pending_slots.peek(session_id)`（追问时登记「还差什么」）；
- 表单字段 = 缺失槽位，标签复用 `agent.write_flow.SLOT_LABELS` 的人话；
- 平台给**候选胶囊**（京东/天猫/淘宝/得物/其它），日期给示例占位；
- 提交 → 合并进原槽位 → 重跑 `write_flow.start`（与文字补槽完全同一条路径）；
- 文字补槽仍然可用（表单只是更快的一条路）。

分层：只调 `agent/`（pending_slots / write_flow），不碰 tools。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import pending_slots, write_flow

import premium_ui
import time_inputs

#: 平台候选（不是业务数据，只是输入便利）
PLATFORM_CHOICES = ("京东", "天猫", "淘宝", "得物", "其它")

#: 各槽位的输入提示（纯文案，不含数字断言）
PLACEHOLDERS = {
    "contact": "例如 王五 / 妈妈",
    "payee": "例如 王五 / 房东（标签也行）",
    "payee_ids": "多人用、分隔，例如 王五、赵六",
    "date": "例如 2027-10-10 或 后天",
    "schedule": "例如 2026-11-01 09:00",
    "budget": "例如 300（元）",
    "amount": "例如 100（元）",
    "address": "例如 上海市某某路 1 号",
    "order_no": "例如 JD20261024001",
    "tags": "多个用、分隔，例如 妈妈、家人",
    "note": "例如 别转错",
    "card_no": "完整卡号（只存后四位）",
    "quota": "例如 300（元/月）",
    "expire": "例如 2026-12-31 或 年底",
    "reason": "例如 总是赖账",
    "weekday": "例如 每周五",
}


def _label(slot: str) -> str:
    """槽位 → 人话（复用 `write_flow.SLOT_LABELS`，缺的按原键名显示）。"""
    return write_flow.SLOT_LABELS.get(slot, slot)


def _field(slot: str) -> str:
    """一个槽位对应的输入控件；返回用户填的内容（空串 = 没填）。"""
    label = _label(slot)
    key = f"slot-form-{slot}"
    if slot == "platform":
        choice = st.selectbox(label, ("", *PLATFORM_CHOICES), key=key)
        return str(choice or "")
    #: 2026-10-29（用户要求）：**所有**时间类槽位都用日历，不再手敲字符串。
    if slot == "schedule":                       # 预约时间 = 日期 + 时刻
        value, too_early = time_inputs.datetime_field(label, key)
        st.session_state[f"{key}-too-early"] = too_early
        return "" if too_early else value         # 过去的时刻当作"没填"
    if slot in ("date", "expire"):               # 送礼日期 / 白名单有效期 = 只选日期
        return time_inputs.date_field(label, key)
    return str(st.text_input(label, key=key,
                             placeholder=PLACEHOLDERS.get(slot, "")) or "").strip()


def _submit(executor: ThreadPoolExecutor, session_id: str, item: dict, values: dict) -> None:
    """表单回灌统一编排，记录当前回执及可审计的确认状态。"""
    import forms
    turn = executor.submit(pending_slots.submit_form, session_id, values).result()
    forms._record(st.session_state, turn, '（表单填写）')
    st.rerun()


def _card_picker(executor: ThreadPoolExecutor, session_id: str, item: dict,
                 other_missing: list[str]) -> None:
    """「选择收款人的银行卡」卡面：全部列出（无默认卡概念），点一张就继续。"""
    from agent import payee_card_flow

    slots = dict(item.get("slots") or {})
    payee_id = str(slots.get("payee_id") or "")
    cards = executor.submit(payee_card_flow.cards_of, payee_id).result()
    name = str(slots.get("payee_name") or "")
    with st.container(border=True, key="payee-card-picker"):
        premium_ui.card_head("🏦 选择收款人的银行卡",
                             f"{name} 有 {len(cards)} 张卡，请指定这次转到哪一张" if cards
                             else "这位收款人还没有登记银行卡", tone="warn")
        if not cards:
            st.caption("先到「收款人」里给他补一张卡，或换一位收款人。")
            return
        for card in cards:
            label = f"{card['bank']} {card['card_no_mask']}"
            if st.button(label, key=f"pick-card-{card['card_id']}", width="stretch",
                         type="primary"):
                turn = executor.submit(pending_slots.submit_form, session_id,
                                       {"payee_card": card["card_id"]}).result()
                import forms
                forms._record(st.session_state, turn, f"（选择收款卡：{label}）")
                st.rerun()
        if other_missing:
            st.caption("同时还需要：" + "、".join(_label(slot) for slot in other_missing))


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """有未完成的补槽 → 画一张表单卡；没有就不画。"""
    item = executor.submit(pending_slots.peek, session_id).result()
    if not item or item.get('intent') == 'gift_plan':
        return
    missing = [slot for slot in (item.get("missing") or []) if slot]
    #: 2026-10-28（用户要求）：群收款由卡面自己收集（总额/选人/按人金额），
    #: 不再弹通用表单 —— 否则一张页面出现「每人金额卡 + 还差几项」两块，很散。
    if item.get("intent") == "aa_collect":
        return
    #: 2026-10-29（用户实测）：同名候选还在「解析卡」里等用户点选时，
    #: 这里再弹表单问"收款人是？"等于让人重填 —— 先等解析卡选完。
    from agent import payee_picker

    if executor.submit(payee_picker.pending, session_id).result():
        return
    if not missing:
        return
    #: 2026-10-31（用户要求）：缺的是「收款卡」时，不画文本框 —— 直接给**选卡卡面**。
    if "payee_card" in missing:
        _card_picker(executor, session_id, item, [slot for slot in missing if slot != "payee_card"])
        return
    with st.container(border=True):
        premium_ui.card_head("📝 还差这几项，直接填就好", "填完点「继续」；也可以照常在下面用一句话回答",
                             tone="warn")
        with st.form("slot-form", clear_on_submit=False):
            values = {slot: _field(slot) for slot in missing}
            if st.form_submit_button("继续", type="primary"):
                _submit(executor, session_id, item, values)
