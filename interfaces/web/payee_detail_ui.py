"""收款人「专属详情卡」v2（2026-10-25 观感升级）。

相比 v1 的改动：
- 人名从 `radio` 改成**横向胶囊**（`st.pills`），一眼扫完、点一下即切换；人多时给搜索框；
- 卡头统一色条标类型（白名单绿 / 黑名单红 / 普通灰），类型用 `st.badge` 语义色；
- 白名单额度改用**进度条**（本月已用 / 总额度），比一行字直观；
- 标签做成彩色 chips，备注单独一行淡色字；
- 操作分级：「转账」主色大按钮，管理类次要，删除靠最右；
- 改额度/有效期/备注/标签收进「编辑资料」折叠区，主卡面只留**信息 + 高频操作**。

分层：只调 `agent.payee_resolve` / `agent.payee_risk` 与 `write_flow.start`（经 `_dispatch`）。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

import streamlit as st

from agent import confirm_card, payee_risk, write_flow

import premium_ui

KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}
TONE = {"whitelist": "ok", "blacklist": "danger", "normal": ""}
BADGE = {"whitelist": ":green-badge[白名单]", "blacklist": ":red-badge[黑名单]",
         "normal": ":gray-badge[普通]"}


def _kind_of(person: dict) -> str:
    if person["blacklist"]:
        return "blacklist"
    return "whitelist" if person["is_whitelist"] else "normal"


def _dispatch(executor: ThreadPoolExecutor, session_id: str, intent: str,
              slots: dict, text: str) -> None:
    """与聊天框同一套：先清在途卡，再起写流程（按钮直达，不再走意图识别）。"""
    confirm_card.clear(session_id)
    future: Future = executor.submit(write_flow.start, intent, slots, session_id)
    st.session_state["last-turn"] = future.result()
    st.session_state["last-turn-text"] = text
    for key in ("pending-payees", "pending-schedules", "pending-aa", "pending-gift"):
        st.session_state[key] = True
    st.rerun()


def _quota_block(executor: ThreadPoolExecutor, person: dict) -> None:
    """白名单额度：进度条 + 已用/剩余（数字全部由 `payee_risk` 算）。"""
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


def _tags(person: dict) -> None:
    tags = person.get("tags") or []
    if tags:
        st.markdown(" ".join(f":blue-badge[{tag}]" for tag in tags))
    note = person.get("note")
    if note:
        st.caption(f"备注：{note}")


def render(executor: ThreadPoolExecutor, session_id: str, people: list[dict]) -> None:
    """人名胶囊 + 该收款人的专属操作卡。"""
    labels = {f"{p['name']}（{p['masked_phone']}）": p for p in people}
    keys = list(labels)
    if len(people) > 6:                                   # 人多给搜索，别让胶囊溢出
        keyword = st.text_input("搜索姓名 / 手机号", key="payee-search",
                                placeholder="输入几个字即可")
        if keyword.strip():
            keys = [k for k in keys if keyword.strip() in k] or keys
    picked = st.pills("选择收款人", keys, key="payee-detail-pick",
                      label_visibility="collapsed")
    person = labels[picked or keys[0]]
    kind = _kind_of(person)
    name = person["name"]
    who = {"payee_id": person["payee_id"], "payee": name,
           "masked_phone": person["masked_phone"]}

    with st.container(border=True):
        premium_ui.card_head(f"{name} · {person['masked_phone']}",
                             f"银行 {person.get('bank') or '—'}　{BADGE[kind]}",
                             tone=TONE[kind])
        _tags(person)
        _quota_block(executor, person)
        expire = person.get("whitelist_expire") or "长期"
        card_no = person.get("card_no") or "未记录"
        recent = (f"最近转账 {str(person['last_used_ts'])[:10]}"
                  if person.get("last_used_ts") else "还没有转账记录")
        st.caption(f"卡号 {card_no} · 白名单有效期 {expire} · {recent}")

        main_col, manage_col = st.columns([1, 3], vertical_alignment="center")
        if main_col.button("转账", key=f"d-tr-{person['payee_id']}", type="primary",
                           width="stretch"):
            _dispatch(executor, session_id, "transfer_single", who, f"（按钮）给{name}转账")
        with manage_col:
            a, b, _c, d = st.columns(4)
            if kind == "normal" and a.button("加白名单", key=f"d-wl-{person['payee_id']}",
                                             width="stretch"):
                _dispatch(executor, session_id, "payee_whitelist", who,
                          f"（按钮）把{name}加入白名单")
            if kind == "whitelist" and a.button("移出白名单", key=f"d-wlrm-{person['payee_id']}",
                                                width="stretch"):
                _dispatch(executor, session_id, "payee_whitelist_remove", who,
                          f"（按钮）把{name}移出白名单")
            if kind == "normal" and b.button("拉黑", key=f"d-bl-{person['payee_id']}",
                                             width="stretch"):
                _dispatch(executor, session_id, "payee_blacklist", who,
                          f"（按钮）把{name}加入黑名单")
            if kind == "blacklist" and b.button("解除黑名单", key=f"d-ub-{person['payee_id']}",
                                                width="stretch"):
                _dispatch(executor, session_id, "payee_unblacklist", who,
                          f"（按钮）把{name}移出黑名单")
            if d.button("删除", key=f"d-del-{person['payee_id']}", width="stretch"):
                _dispatch(executor, session_id, "payee_delete", who, f"（按钮）删除收款人{name}")

        with st.expander("▸ 编辑资料（额度 / 有效期 / 备注 / 标签）", expanded=False):
            if kind == "whitelist":
                col_a, col_b = st.columns(2)
                quota = col_a.text_input("新的专属额度（元/月，留空=不改）",
                                         key=f"d-q-{person['payee_id']}")
                expire_new = col_b.text_input("新的有效期（留空=不改）",
                                              key=f"d-e-{person['payee_id']}",
                                              placeholder="2026-12-31 或 月底")
                if st.button("保存白名单信息（需验证码）", key=f"d-q-ok-{person['payee_id']}"):
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
