"""收款人列表 v3：**列表（图一）→ 点某人在原位展开详情（图二）**，带拉开动画。

用户 2026-10-28 的原话：
- 「像图一的这种列表，保存」→ 保留一长条列表（头像 + 姓名 + 脱敏手机 + 银行 + 类型徽章）；
- 「点击某个人的选项后会拉开（可以有一个拉开的动画效果）一条展示这个人的详细信息」→
  行尾 ▸ 展开，**在这一行下方**渲染详情（不再跳到页面底部、也不再另开一张卡）；
- 「编辑收款人的选项放在收款人的名字之下，并放大一点」→ 详情里第一块就是大号「编辑资料」。

分层：只调 `agent/`（`payee_risk` 算额度剩余、`write_flow.start` 走写流程），不碰 tools。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import confirm_card, payee_risk, pending_slots, write_flow

import premium_ui
import step_record
import time_inputs

KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}
TONE = {"whitelist": "ok", "blacklist": "danger", "normal": ""}
BADGE = {"whitelist": ("✅ 白名单", "#9bd3b0", "#183026"),
         "blacklist": ("⛔ 黑名单", "#f0a8a8", "#3a1d1d"),
         "normal": ("· 普通", "#b8bdc8", "#23252b")}


def _kind_of(person: dict) -> str:
    if person["blacklist"]:
        return "blacklist"
    return "whitelist" if person["is_whitelist"] else "normal"


def _dispatch(executor: ThreadPoolExecutor, session_id: str, intent: str,
              slots: dict, text: str) -> None:
    """按钮直达：清在途卡 → `write_flow.start` → 回执记进会话（与 `app._ask` 同口径）。"""
    confirm_card.clear(session_id)
    if intent in ('payee_whitelist_edit', 'payee_note', 'payee_tags', 'transfer_single'):
        st.session_state['payee-inline-intent'] = intent
        st.session_state.pop('payee-edit-feedback', None)
    step = executor.submit(write_flow.start, intent, slots, session_id).result()
    if (st.session_state.get('payee-inline-intent') == intent
            and executor.submit(confirm_card.current, session_id).result() is None):
        st.session_state['payee-edit-feedback'] = step.reply or step.ask or ''
    #: 2026-10-30：回执统一用 step_record 记（带 pending_id，L3 待复核卡才显示）
    step_record.record_step(step, text)
    st.rerun()


def _quota_block(executor: ThreadPoolExecutor, person: dict) -> None:
    """白名单额度：进度条 + 已用/剩余（数字由 `payee_risk` 算，界面不自己算）。"""
    if not person["is_whitelist"] or person.get("whitelist_quota") is None:
        return
    total = person["whitelist_quota"] / 100
    left = executor.submit(payee_risk.remaining_quota, person).result()
    if left is None:
        st.caption(f"专属额度 {total:,.2f} 元/月（每月重置）")
        return
    used = max(0.0, total - left / 100)
    st.progress(min(1.0, used / total) if total else 0.0,
                text=f"本月已用 {used:,.2f} / {total:,.2f} 元（剩余 {left / 100:,.2f}，每月重置）")


def _editor(executor: ThreadPoolExecutor, session_id: str, person: dict, who: dict) -> None:
    """「编辑资料」——放在**名字正下方**，字号放大（用户要求）。"""
    kind, name = _kind_of(person), person["name"]
    with st.expander("✏️ 编辑资料（额度 / 有效期 / 备注 / 标签）", expanded=False):
        if kind == "whitelist":
            col_a, col_b = st.columns(2)
            quota = col_a.text_input("新的专属额度（元/月，留空=不改）",
                                     key=f"d-q-{person['payee_id']}")
            #: 2026-10-29：有效期改用日历（留空=不改 → 用「是否调整」勾选表达）
            expired_edit = col_b.checkbox("调整有效期", key=f"d-e-on-{person['payee_id']}")
            expire_new = (time_inputs.date_field("新的有效期", f"d-e-{person['payee_id']}")
                          if expired_edit else "")
            if st.button("保存白名单信息（需验证码）", key=f"d-q-ok-{person['payee_id']}",
                         disabled=not (quota.strip() or expire_new.strip())):
                slots = dict(who)
                if quota.strip():
                    slots["quota"] = quota.strip()
                if expire_new.strip():
                    slots["expire"] = expire_new.strip()
                _dispatch(executor, session_id, "payee_whitelist_edit", slots,
                          f"（按钮）修改{name}的白名单信息")
        col_n, col_t = st.columns(2)
        note_new = col_n.text_input("备注（留空=清除）", key=f"d-note-{person['payee_id']}")
        tags_new = col_t.text_input("标签（多个用、分隔）", key=f"d-tags-{person['payee_id']}")
        if st.button("保存备注 / 标签", key=f"d-note-ok-{person['payee_id']}"):
            if note_new.strip() != (person.get("note") or ""):
                _dispatch(executor, session_id, "payee_note",
                          {**who, "note": note_new.strip()}, f"（按钮）改{name}的备注")
            if tags_new.strip():
                _dispatch(executor, session_id, "payee_tags",
                          {**who, "tags": tags_new.strip()}, f"（按钮）改{name}的标签")


def _actions(executor: ThreadPoolExecutor, session_id: str, person: dict, who: dict) -> None:
    """操作按钮：**四个等宽等距**（转账 / 名单切换 / 拉黑·解除 / 删除）。

    用户 2026-10-28（配图三）：原来「转账」独占一列、其余挤在 4 列里，宽窄不一。
    现在统一一行四列、`width="stretch"`，删除也在这行最右（不再拉长到整行）。
    """
    kind, name = _kind_of(person), person["name"]
    col_a, col_b, col_c, col_d = st.columns(4, vertical_alignment="center")

    if col_a.button("转账", key=f"d-tr-{person['payee_id']}", type="primary", width="stretch"):
        executor.submit(confirm_card.clear, session_id).result()
        executor.submit(pending_slots.clear, session_id).result()
        st.session_state['payee-inline-intent'] = 'transfer_single'
        st.session_state.pop('payee-edit-feedback', None)
        st.session_state.pop('payee-inline-done', None)
        st.rerun()

    if kind == "whitelist":
        if col_b.button("移出白名单", key=f"d-wlrm-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist_remove", who,
                      f"（按钮）把{name}移出白名单")
    else:                                        # 普通 / 黑名单 → 直接加白
        if col_b.button("加白名单", key=f"d-wl-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist", who, f"（按钮）把{name}加入白名单")

    if kind == "blacklist":
        if col_c.button("解除黑名单", key=f"d-ub-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_unblacklist", who, f"（按钮）把{name}移出黑名单")
    else:
        if col_c.button("拉黑", key=f"d-bl-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_blacklist", who, f"（按钮）把{name}加入黑名单")

    if col_d.button("删除", key=f"d-del-{person['payee_id']}", width="stretch"):
        _dispatch(executor, session_id, "payee_delete", who, f"（按钮）删除收款人{name}")


def _detail(executor: ThreadPoolExecutor, session_id: str, person: dict, confirmation=None) -> None:
    """展开的详情：名字色条 → **编辑资料（放大）** → 标签 → 额度进度 → 元信息 → 操作。"""
    kind = _kind_of(person)
    who = {"payee_id": person["payee_id"], "payee": person["name"],
           "masked_phone": person["masked_phone"]}
    with st.container(border=True, key="payee-detail"):
        verifying = bool(st.session_state.get('payee-inline-intent')) and confirmation is not None
        main, side = st.columns([1.5, 1], gap='medium') if verifying else (st.container(), None)
        with main:
            premium_ui.card_head(f"{person['name']} · {person['masked_phone']}",
                                 f"银行 {person.get('bank') or '—'}　{KIND_CN[kind]}", tone=TONE[kind])
            _editor(executor, session_id, person, who)          # ← 名字之下、放大
            tags = person.get("tags") or []
            if tags:
                st.markdown(" ".join(f":blue-badge[{tag}]" for tag in tags))
            if person.get("note"):
                st.caption(f"备注：{person['note']}")
            _quota_block(executor, person)
            expire = person.get("whitelist_expire") or "长期"
            card_no = person.get("card_no") or "未记录"
            recent = (f"最近转账 {str(person['last_used_ts'])[:10]}"
                      if person.get("last_used_ts") else "还没有转账记录")
            st.caption(f"卡号 {card_no} · 白名单有效期 {expire} · {recent}")
            _actions(executor, session_id, person, who)
        if side is not None:
            with side, st.container(border=True, key="payee-verification"):
                transferring = st.session_state.get('payee-inline-intent') == 'transfer_single'
                st.markdown('##### 转账' if transferring else '##### 保存修改')
                st.caption(f"{person['name']} · {person['masked_phone']}")
                draft = executor.submit(confirm_card.current, session_id).result()
                if draft and draft.intent == st.session_state.get('payee-inline-intent'):
                    if feedback := st.session_state.get('payee-edit-feedback'):
                        st.info(feedback)
                    confirmation(inline=True)
                else:
                    if feedback := st.session_state.get('payee-edit-feedback'):
                        st.info(feedback)
                    if transferring and not st.session_state.get('payee-inline-done'):
                        _transfer_amount(executor, session_id, person, who)
                    if st.button('取消转账' if transferring else '收起', key='payee-verification-close'):
                        st.session_state.pop('payee-inline-intent', None)
                        st.session_state.pop('payee-edit-feedback', None)
                        st.session_state.pop('payee-inline-done', None)
                        executor.submit(pending_slots.clear, session_id).result()
                        st.rerun()



def _transfer_amount(executor, session_id: str, person: dict, who: dict) -> None:
    """Collect the amount beside its recipient; preview and execution stay in the write flow."""
    with st.form('payee-transfer-' + str(person['payee_id'])):
        amount = st.text_input('转账金额（元）', key='payee-transfer-amount-' + str(person['payee_id']),
                               placeholder='请输入转账金额')
        submitted = st.form_submit_button('核对转账', type='primary', width='stretch')
    if submitted:
        if not amount.strip():
            st.warning('请输入转账金额。')
            return
        _dispatch(executor, session_id, 'transfer_single', {**who, 'amount': amount.strip()},
                  f"（按钮）给{person['name']}转账")


def _row_label(person: dict, kind: str) -> str:
    """整行按钮的文案：姓名 + 脱敏号 + 银行 + 类型（一行内对齐）。"""
    return (f"**{person['name']}** {person['masked_phone']}"
            f" {person.get('bank') or '—'} {KIND_CN[kind]}")


def render(executor: ThreadPoolExecutor, session_id: str, people: list[dict], confirmation=None) -> None:
    """一列「整行按钮」；点谁就在谁下面拉开详情（再点收起）。"""
    opened = st.session_state.get("payee-open")
    for person in people:
        payee_id = str(person["payee_id"])
        kind = _kind_of(person)
        arrow = "▾" if opened == payee_id else "▸"
        #: 包一层 keyed container，CSS 才能把这一行的按钮改成「列表行」观感
        with st.container(key=f"payee-row-{payee_id}"):
            if st.button(f"{_row_label(person, kind)}", key=f"payee-open-{payee_id}",
                         width="stretch", help=f"点这一行{'收起' if opened == payee_id else '展开'}详情"):
                if st.session_state.get('payee-inline-intent'):
                    executor.submit(confirm_card.clear, session_id).result()
                for key in ('payee-inline-intent', 'payee-edit-feedback', 'payee-inline-done'):
                    st.session_state.pop(key, None)
                st.session_state["payee-open"] = None if opened == payee_id else payee_id
                st.rerun()
            st.caption(arrow) if False else None
        if opened == payee_id:
            _detail(executor, session_id, person, confirmation)
