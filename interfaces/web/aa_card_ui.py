"""群收款卡（2026-10-29 三卡合一）：参考微信「群收款」的单页形态。

一屏之内：金额 → 参与人（多选/全选/确认）→ 备注 → 均摊金额（大字）→ 按人金额 → **确认/发起**。
合规链路不变（预览 → 确认 → 执行），只是把"确认"搬进同一张卡：
- 没有在途确认 → 显示发起表单；
- 有在途确认（`confirm_card.current`）→ 同一张卡里显示卡面摘要 + 「✅ 确认 / ✖ 取消 / 改金额」。

分层：只调 `agent/`（`aa_flow` 出数与名单、`write_flow` 走写流程、`confirm_card` 读在途）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import aa_flow, confirm_card, write_flow

import premium_ui


def _record(step: write_flow.Step, summary: str) -> None:
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": step.reply or step.ask or "",
                              "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})


def _start(executor: ThreadPoolExecutor, session_id: str, intent: str,
           slots: dict, display: str) -> None:
    confirm_card.clear(session_id)
    _record(executor.submit(write_flow.start, intent, slots, session_id).result(), display)
    st.rerun()


def _toggle(key: str) -> None:
    st.session_state[key] = not st.session_state.get(key, False)


def _remember_people(picked: list[str]) -> None:
    st.session_state["aa-people"] = list(picked)
    st.session_state["aa-pick-open"] = False


def _confirm_panel(executor: ThreadPoolExecutor, session_id: str, confirmation) -> None:
    """在途确认：在同一张卡里给「确认 / 取消 / 改金额」。"""
    filled = dict(getattr(confirmation, "evidence", None) or {})
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款 · 确认这一笔",
                             "看一眼再确认；确认后才会真的发出去", tone="warn")
        rows = [("金额（元）", f"{getattr(confirmation, 'amount_cents', 0) / 100:,.2f}"),
                ("收款人", str(filled.get("target_name") or "—")),
                ("每人", f"{filled.get('per_person_amount_yuan', '—')} 元（含我一份）"),
                ("我承担", f"{filled.get('initiator_share_yuan', '—')} 元"),
                ("待收", f"{filled.get('collect_yuan', '—')} 元")]
        for label, value in rows:
            st.markdown(f"**{label}**　{value}")
        left, middle, right = st.columns(3, vertical_alignment="center")
        if left.button("✅ 确认", key="aa-confirm-go", type="primary", width="stretch"):
            _record(executor.submit(write_flow.confirm, session_id).result(), "（按钮）确认群收款")
            st.rerun()
        if middle.button("✖ 取消", key="aa-confirm-cancel", width="stretch"):
            _record(executor.submit(write_flow.cancel, session_id).result(), "（按钮）取消群收款")
            st.rerun()
        amount_new = right.text_input("改金额（元）", key="aa-confirm-amount",
                                      label_visibility="collapsed", placeholder="改金额")
        if amount_new.strip() and right.button("按新金额重算", key="aa-confirm-edit",
                                               width="stretch"):
            step = executor.submit(write_flow.edit, session_id,
                                   {"amount": amount_new.strip()}).result()
            _record(step, "（群收款金额已修改）")
            st.rerun()


def _create_panel(executor: ThreadPoolExecutor, session_id: str) -> None:
    """发起表单：金额 / 参与人 / 备注 / 均摊 / 按人金额 / 发起。"""
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款（含你自己）",
                             "填金额 → 选参与人 → 发起；每人均摊，你承担自己那一份", tone="")
        total = st.number_input("群收款总金额（元）", min_value=0.0, step=10.0, key="aa-total",
                                format="%.2f")
        title = st.text_input("备注（事由）", key="aa-title", placeholder="例如 出去吃饭")

        if st.button("参与人数" + ("  ▾" if st.session_state.get("aa-pick-open") else "  ▸"),
                     key="aa-pick-toggle", width="stretch"):
            _toggle("aa-pick-open")
        if st.session_state.get("aa-pick-open"):
            names = executor.submit(aa_flow.candidates).result()
            picked = st.multiselect("选择参与人（可搜索、可全选）", names, key="aa-picked",
                                    placeholder="还没有选择任何人")
            if st.button("全选", key="aa-pick-all", width="stretch"):
                _remember_people(list(names))
                st.rerun()
            st.button("确认参与人", key="aa-pick-confirm", type="primary",
                      disabled=not picked, on_click=_remember_people, args=(picked,))

        people = list(st.session_state.get("aa-people") or [])
        if not people:
            st.caption("还没有确认参与人 —— 选好后点「确认参与人」。")
            return
        plan = executor.submit(aa_flow.group_plan, people, float(total or 0)).result()
        st.markdown(f"<div style='text-align:center;margin:.4rem 0 .2rem'>"
                    f"<span style='color:#9aa0ac;font-size:.85rem'>均摊金额（含我）</span><br>"
                    f"<span style='font-size:2.1rem;font-weight:650'>¥ {plan['per_yuan']}</span>"
                    f"</div>", unsafe_allow_html=True)
        st.caption(f"{plan['count']} 人（含我）· 我承担 {plan['mine_yuan']} 元 · "
                   f"待收 {plan['collect_yuan']} 元 · 参与人：{'、'.join(people)}")

        if st.button("按人设置金额" + ("  ▾" if st.session_state.get("aa-split-open") else "  ▸"),
                     key="aa-split-toggle", width="stretch"):
            _toggle("aa-split-open")
        amounts: dict[str, str] = {}
        if st.session_state.get("aa-split-open"):
            for index, name in enumerate(people):
                amounts[name] = st.text_input(f"{name} 应收（元）", key=f"aa-amt-{index}",
                                              value=plan["per_yuan"])

        if st.button("发起群收款", key="aa-create-go", type="primary", width="stretch",
                     disabled=float(total or 0) <= 0):
            slots = {"payee_ids": people, "amount": plan["total_yuan"]}
            if title.strip():
                slots["title"] = title.strip()
            filled = {name: value.strip() for name, value in amounts.items() if value.strip()}
            if filled:
                slots["amounts"] = "、".join(f"{name} {value}" for name, value in filled.items())
            _start(executor, session_id, "aa_collect", slots, "（按钮）发起群收款：" + "、".join(people))


def _status_card(executor: ThreadPoolExecutor, session_id: str, item: dict) -> None:
    """付款状态卡：谁已付 / 谁没付 + 登记已付 + 撤销。"""
    request_id = str(item["request_id"])
    with st.container(border=True, key=f"aa-status-{request_id}"):
        premium_ui.card_head(f"🧾 {item['title']}",
                             f"待收 {item.get('collect_yuan', item['total_yuan'])} 元 · "
                             f"已付 {item['paid_count']}/{item['share_count']} 人 · "
                             f"截止 {item['expire_at']}", tone="ok")
        for share in list(item.get("shares") or []):
            done = share["status"] == "paid"
            st.markdown(f"{'✅' if done else '⏳'} **{share['payee_name']}** · "
                        f"{share['amount_yuan']} 元 · {'已付' if done else '未付'}")
        pending = [share for share in (item.get("shares") or []) if share["status"] != "paid"]
        left, right = st.columns([3, 1], vertical_alignment="center")
        if pending:
            names = {f"{share['payee_name']}（{share['amount_yuan']} 元）": share["payee_id"]
                     for share in pending}
            picked = left.selectbox("登记谁已付", list(names), key=f"aa-who-{request_id}",
                                    label_visibility="collapsed")
            if right.button("登记已付", key=f"aa-paid-{request_id}", type="primary",
                            width="stretch"):
                _start(executor, session_id, "aa_mark_paid",
                       {"request_id": request_id, "payee_id": names[picked]},
                       f"（按钮）登记 {picked} 已付")
        else:
            left.caption("这笔群收款已全部付清 ✅")
        if right.button("撤销", key=f"aa-cancel-{request_id}", width="stretch"):
            _start(executor, session_id, "aa_cancel", {"request_id": request_id},
                   f"（按钮）撤销群收款 {item['title']}")


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """一张卡（发起或在途确认）+ 每笔付款状态卡。"""
    confirmation = executor.submit(confirm_card.current, session_id).result()
    if confirmation is not None and getattr(confirmation, "intent", "") == "aa_collect":
        _confirm_panel(executor, session_id, confirmation)
    else:
        _create_panel(executor, session_id)
    for item in executor.submit(aa_flow.active).result():
        _status_card(executor, session_id, item)
