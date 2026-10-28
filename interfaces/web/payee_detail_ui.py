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

from agent import confirm_card, payee_risk, write_flow

import premium_ui

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
    step = executor.submit(write_flow.start, intent, slots, session_id).result()
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": step.reply or step.ask or "",
                              "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": text, "turn": None})
    st.rerun()


def _row_html(person: dict) -> str:
    """一行列表：圆形头像（姓首字）+ 姓名 + 脱敏手机 + 银行（观感对齐图一）。"""
    initial = str(person["name"])[:1] or "?"
    return (f'<div class="dsh-payee-row">'
            f'<span class="dsh-payee-avatar">{initial}</span>'
            f'<span class="dsh-payee-name">{person["name"]}</span>'
            f'<span class="dsh-payee-phone">{person["masked_phone"]}</span>'
            f'<span class="dsh-payee-bank">{person.get("bank") or "—"}</span>'
            f'</div>')


def _badge_html(kind: str) -> str:
    text, color, background = BADGE[kind]
    return (f'<div style="text-align:right"><span style="font-size:12px;padding:2px 10px;'
            f'border-radius:999px;color:{color};background:{background};'
            f'white-space:nowrap">{text}</span></div>')


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
            expire_new = col_b.text_input("新的有效期（留空=不改）", key=f"d-e-{person['payee_id']}",
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


def _actions(executor: ThreadPoolExecutor, session_id: str, person: dict, who: dict) -> None:
    """操作按钮：转账（主色）+ 白/黑名单切换 + 删除（删除靠最右）。"""
    kind, name = _kind_of(person), person["name"]
    main_col, manage_col = st.columns([1, 3], vertical_alignment="center")
    if main_col.button("转账", key=f"d-tr-{person['payee_id']}", type="primary", width="stretch"):
        _dispatch(executor, session_id, "transfer_single", who, f"（按钮）给{name}转账")
    with manage_col:
        a, b, _c, d = st.columns(4)
        if kind == "normal" and a.button("加白名单", key=f"d-wl-{person['payee_id']}",
                                         width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist", who, f"（按钮）把{name}加入白名单")
        if kind == "whitelist" and a.button("移出白名单", key=f"d-wlrm-{person['payee_id']}",
                                            width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist_remove", who,
                      f"（按钮）把{name}移出白名单")
        if kind == "blacklist" and a.button("加白名单", key=f"d-wl2-{person['payee_id']}",
                                            width="stretch"):
            _dispatch(executor, session_id, "payee_whitelist", who, f"（按钮）把{name}加入白名单")
        if kind != "blacklist" and b.button("拉黑", key=f"d-bl-{person['payee_id']}",
                                            width="stretch"):
            _dispatch(executor, session_id, "payee_blacklist", who, f"（按钮）把{name}加入黑名单")
        if kind == "blacklist" and b.button("解除黑名单", key=f"d-ub-{person['payee_id']}",
                                            width="stretch"):
            _dispatch(executor, session_id, "payee_unblacklist", who,
                      f"（按钮）把{name}移出黑名单")
        if d.button("删除", key=f"d-del-{person['payee_id']}", width="stretch"):
            _dispatch(executor, session_id, "payee_delete", who, f"（按钮）删除收款人{name}")


def _detail(executor: ThreadPoolExecutor, session_id: str, person: dict) -> None:
    """展开的详情：名字色条 → **编辑资料（放大）** → 标签 → 额度进度 → 元信息 → 操作。"""
    kind = _kind_of(person)
    who = {"payee_id": person["payee_id"], "payee": person["name"],
           "masked_phone": person["masked_phone"]}
    with st.container(border=True, key="payee-detail"):
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


def render(executor: ThreadPoolExecutor, session_id: str, people: list[dict]) -> None:
    """一长条列表；点行尾 ▸ 在该行下方展开详情（带拉开动画）。"""
    opened = st.session_state.get("payee-open")
    premium_ui.card_head(f"👥 收款人管理（{len(people)} 位）",
                         "点每行右侧 ▸ 展开这位收款人的详情与操作", tone="")
    for person in people:
        payee_id = str(person["payee_id"])
        kind = _kind_of(person)
        with st.container(key=f"payee-row-{payee_id}"):
            left, right, toggle = st.columns([7.2, 2.2, 0.6], vertical_alignment="center")
            left.markdown(_row_html(person), unsafe_allow_html=True)
            right.markdown(_badge_html(kind), unsafe_allow_html=True)
            arrow = "▾" if opened == payee_id else "▸"
            if toggle.button(arrow, key=f"payee-toggle-{payee_id}",
                             help="展开 / 收起这位收款人的详情"):
                st.session_state["payee-open"] = None if opened == payee_id else payee_id
                st.rerun()
        if opened == payee_id:
            _detail(executor, session_id, person)
