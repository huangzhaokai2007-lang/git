"""收款人「专属详情卡」：先选人，再在这一个人的界面上做所有操作（2026-10-24 用户设计）。

以前是一长串「每个人一行 + 一排按钮」，信息挤在一行、改额度/改备注在页面最底部另开表单。
现在：**筛选 → 人名选项 → 该收款人的专属卡片**（基础信息 + 操作 + 改额度有效期 + 改备注/标签）。

分层：只调 `agent.payee_resolve` / agent 的写入口（`write_flow.start` 经 `_dispatch`），不碰 tools。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

import streamlit as st

from agent import confirm_card, payee_resolve, write_flow

KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}
KIND_BADGE = {"whitelist": "✅ 白名单", "blacklist": "⛔ 黑名单", "normal": "· 普通"}


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


def _facts_line(executor: ThreadPoolExecutor, person: dict) -> str:
    """额度与剩余（走工具层算，界面不自己算数字）。"""
    if not person["is_whitelist"] or person.get("whitelist_quota") is None:
        return ""
    from agent import payee_risk

    left = executor.submit(payee_risk.remaining_quota, person).result()
    total = person["whitelist_quota"] / 100
    if left is None:
        return f" · 专属额度 {total:,.2f} 元/月"
    return f" · 专属额度 {total:,.2f} 元/月（剩余 {left / 100:,.2f} 元）"


def render(executor: ThreadPoolExecutor, session_id: str, people: list[dict]) -> None:
    """人名选项 + 该收款人的专属操作卡。"""
    labels = {}
    for person in people:
        kind = KIND_CN[_kind_of(person)]
        labels[f"{person['name']}（{person['masked_phone']}）· {kind}"] = person
    choice = st.radio("选择收款人", list(labels), key="payee-detail-pick",
                      label_visibility="collapsed")
    person = labels[choice]
    kind = _kind_of(person)
    who = {"payee_id": person["payee_id"], "payee": person["name"],
           "masked_phone": person["masked_phone"]}
    name = person["name"]

    with st.container(border=True):
        st.markdown(f"##### {name} `{person['masked_phone']}` · {KIND_BADGE[kind]}")
        tags = "、".join(person.get("tags") or []) or "（无）"
        note = person.get("note") or "（无）"
        card_no = person.get("card_no") or "（未记录）"
        expire = person.get("whitelist_expire") or "长期"
        recent = ("· 最近转账 " + str(person["last_used_ts"])[:10]) if person.get("last_used_ts") else ""
        st.caption(f"银行 {person.get('bank') or '—'} · 标签 {tags} · 备注 {note}")
        st.caption(f"卡号 {card_no} · 白名单有效期 {expire}"
                   f"{_facts_line(executor, person)} {recent}")

        cols = st.columns([1, 1, 1, 1], vertical_alignment="center")
        if cols[0].button("转账", key=f"d-tr-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "transfer_single", who, f"（按钮）给{name}转账")
        if kind == "normal" and cols[1].button("加白名单", key=f"d-wl-{person['payee_id']}",
                                              width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist", who, f"（按钮）把{name}加入白名单")
        if kind == "whitelist" and cols[1].button("移出白名单", key=f"d-wlrm-{person['payee_id']}",
                                                  width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist_remove", who,
                      f"（按钮）把{name}移出白名单")
        if kind == "normal" and cols[2].button("拉黑", key=f"d-bl-{person['payee_id']}",
                                              width="stretch"):
            _dispatch(executor, session_id, "payee_blacklist", who, f"（按钮）把{name}加入黑名单")
        if kind == "blacklist" and cols[2].button("解除黑名单", key=f"d-ub-{person['payee_id']}",
                                                  width="stretch"):
            _dispatch(executor, session_id, "payee_unblacklist", who, f"（按钮）把{name}移出黑名单")
        if cols[3].button("删除", key=f"d-del-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_delete", who, f"（按钮）删除收款人{name}")

        if kind == "whitelist":                             # 改额度/有效期（L2 + 验证码）
            col_a, col_b, col_c = st.columns([1, 1, 0.6], vertical_alignment="bottom")
            quota = col_a.text_input("新的专属额度（元，留空=不改）", key=f"d-q-{person['payee_id']}")
            expire_new = col_b.text_input("新的有效期（留空=不改）", key=f"d-e-{person['payee_id']}",
                                          placeholder="2026-12-31 或 月底")
            if col_c.button("保存", key=f"d-q-ok-{person['payee_id']}", type="primary",
                            width="stretch"):
                slots = dict(who)
                if quota.strip():
                    slots["quota"] = quota.strip()
                if expire_new.strip():
                    slots["expire"] = expire_new.strip()
                _dispatch(executor, session_id, "payee_whitelist_edit", slots,
                          f"（按钮）修改{name}的白名单信息")

        col_n, col_t, col_s = st.columns([2, 2, 0.6], vertical_alignment="bottom")
        note_new = col_n.text_input("备注（留空=清除）", key=f"d-note-{person['payee_id']}")
        tags_new = col_t.text_input("标签（多个用、分隔，留空=清除）", key=f"d-tags-{person['payee_id']}")
        if col_s.button("保存", key=f"d-note-ok-{person['payee_id']}", width="stretch"):
            if note_new.strip() != (person.get("note") or ""):
                _dispatch(executor, session_id, "payee_note",
                          {**who, "note": note_new.strip()}, f"（按钮）改{name}的备注")
            if tags_new.strip():
                _dispatch(executor, session_id, "payee_tags",
                          {**who, "tags": tags_new.strip()}, f"（按钮）改{name}的标签")
