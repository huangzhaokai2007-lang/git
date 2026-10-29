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

from agent import aa_flow, confirm_card, payee_flow, payee_picker, write_flow

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


def _say(executor: ThreadPoolExecutor, session_id: str, text: str, summary: str) -> None:
    """把一句话交给编排层（按钮 = 打字），然后 rerun。"""
    from agent import orchestrator

    turn = executor.submit(orchestrator.handle, text, session_id=session_id).result()
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})
    st.rerun()


def _toggle(key: str) -> None:
    st.session_state[key] = not st.session_state.get(key, False)


def _remember_people(picked: list[str]) -> None:
    st.session_state["aa-people"] = list(picked)
    st.session_state["aa-people-touched"] = True      # 用户动过名单 → 不再自动预填
    st.session_state["aa-pick-open"] = False


def _add_selected(picked: list[str]) -> None:
    """「添加所选」：把多选里挑出来的人并进参与人（去重）。"""
    current = list(st.session_state.get("aa-people") or [])
    st.session_state["aa-people"] = current + [n for n in picked if n not in current]
    st.session_state["aa-people-touched"] = True
    st.session_state["aa-picked"] = []                      # 回调里清，下次渲染前生效


def _remove_person(name: str) -> None:
    """「删除」某位参与人。"""
    st.session_state["aa-people"] = [p for p in (st.session_state.get("aa-people") or [])
                                     if p != name]
    st.session_state["aa-people-touched"] = True


def _select_all() -> None:
    """「全选」：把候选里还没加的人一次加进来。"""
    current = list(st.session_state.get("aa-people") or [])
    st.session_state["aa-people"] = current


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
        #: 2026-10-29 修（用户实测「点击确认后直接卡 bug」）：这里原来调的是**不存在的**
        #: `write_flow.confirm` / `write_flow.cancel` → AttributeError。
        #: 正确做法是与聊天里说「确认 / 取消」完全同一条路径：交给编排层。
        if left.button("✅ 确认", key="aa-confirm-go", type="primary", width="stretch"):
            _say(executor, session_id, "确认", "（按钮）确认群收款")
        if middle.button("✖ 取消", key="aa-confirm-cancel", width="stretch"):
            _say(executor, session_id, "取消", "（按钮）取消群收款")
        amount_new = right.text_input("改金额（元）", key="aa-confirm-amount",
                                      label_visibility="collapsed", placeholder="改金额")
        if amount_new.strip() and right.button("按新金额重算", key="aa-confirm-edit",
                                               width="stretch"):
            step = executor.submit(write_flow.edit, session_id,
                                   {"amount": amount_new.strip()}).result()
            _record(step, "（群收款金额已修改）")
            st.rerun()


def _preset(executor: ThreadPoolExecutor, session_id: str) -> dict:
    """把用户**上一句已经说过的**人/金额/事由带进卡面（避免"还没选参与人"）。

    2026-10-29（用户实测）：「向李四、张小美、王五群收款」→ 系统追问总金额，
    但卡面参与人却是空的，得重新点选一遍。人明明已经解析出来了，就该直接带出来。
    """
    from agent import pending_slots

    item = executor.submit(pending_slots.peek, session_id).result() or {}
    slots = dict(item.get("slots") or {})
    names = [str(name) for name in (slots.get("aa_names") or []) if str(name).strip()]
    if not names and slots.get("payee_ids"):
        names = [str(pid) for pid in slots["payee_ids"]]
    amount = slots.get("amount")
    try:
        total = float(str(amount)) if amount not in (None, "") else 0.0
    except ValueError:
        total = 0.0
    return {"names": names, "total": total, "title": str(slots.get("title") or "")}


def _create_panel(executor: ThreadPoolExecutor, session_id: str) -> None:
    """发起表单：金额 / 参与人 / 备注 / 均摊 / 按人金额 / 发起。"""
    preset = _preset(executor, session_id)
    if (preset["names"] and not st.session_state.get("aa-people")
            and not st.session_state.get("aa-people-touched")):
        st.session_state["aa-people"] = list(preset["names"])     # 直接带上已解析的人
        st.session_state["aa-picked"] = list(preset["names"])
    if preset["total"] and not st.session_state.get("aa-total"):
        st.session_state["aa-total"] = preset["total"]
    if preset["title"] and not st.session_state.get("aa-title"):
        st.session_state["aa-title"] = preset["title"]
    #: 2026-10-29（用户实测「这又是什么」）：同名多命中时上方已经出了**解析卡**，
    #: 这里再画一张"还没有确认参与人"的群收款卡，两张叠着很怪 —— 等用户点完解析卡再说。
    if executor.submit(payee_picker.pending, session_id).result():
        return
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款（含你自己）",
                             "填金额 → 选参与人 → 发起；每人均摊，你承担自己那一份", tone="")
        total = st.number_input("群收款总金额（元）", min_value=0.0, step=10.0, key="aa-total",
                                format="%.2f")
        title = st.text_input("备注（事由）", key="aa-title", placeholder="例如 出去吃饭")

        if st.button("修改参与人" + ("  ▾" if st.session_state.get("aa-pick-open") else "  ▸"),
                     key="aa-pick-toggle", width="stretch"):
            _toggle("aa-pick-open")
        if st.session_state.get("aa-pick-open"):
            people_now = list(st.session_state.get("aa-people") or [])
            if people_now:
                st.caption("已选参与人（点「删除」移出）：")
                for index, name in enumerate(people_now):
                    row_left, row_right = st.columns([5, 1], vertical_alignment="center")
                    row_left.markdown(f"**{name}**")
                    if row_right.button("删除", key=f"aa-rm-{index}-{name}", width="stretch"):
                        _remove_person(name)
                        st.rerun()
            else:
                st.caption("还没有参与人 —— 从下面挑，或新增一个不在名单里的人。")

            names = executor.submit(aa_flow.candidates).result()
            options = [n for n in names if n not in people_now]
            picked = st.multiselect("添加参与人（可搜索、可多选）", options, key="aa-picked",
                                    placeholder="选择要添加的人")
            col_add, col_all = st.columns(2)
            col_add.button("添加所选", key="aa-add-sel", width="stretch",
                           disabled=not picked, on_click=_add_selected, args=(picked,))
            col_all.button("全选（把候选都加进来）", key="aa-pick-all", width="stretch",
                           on_click=_remember_people, args=(list(names),))
            with st.expander("＋ 新增收款人（不在列表里的人）", expanded=False):
                new_name = st.text_input("姓名", key="aa-new-name")
                new_phone = st.text_input("手机号", key="aa-new-phone", placeholder="138****0001")
                if st.button("添加并计入参与人", key="aa-add-person",
                             disabled=not (new_name.strip() and new_phone.strip())):
                    turn = executor.submit(payee_flow.submit_payee, new_name.strip(),
                                           new_phone.strip(), session_id=session_id).result()
                    if getattr(turn, "executed", False) or "已添加" in (turn.reply or ""):
                        people = list(st.session_state.get("aa-people") or [])
                        if new_name.strip() not in people:
                            people.append(new_name.strip())
                        st.session_state["aa-people"] = people
                    st.rerun()

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
    """一张卡（发起或在途确认）+ 每笔付款状态卡。

    2026-10-29（用户要求）：查看群收款时**不再直接铺开**发起表单 ——
    先给一个「＋ 新增群收款」按钮，点了才拉开；已经在补槽（例如刚说"向王五群收款"缺金额）时自动展开。
    """
    confirmation = executor.submit(confirm_card.current, session_id).result()
    if confirmation is not None and getattr(confirmation, "intent", "") == "aa_collect":
        st.session_state["aa-create-open"] = False       # 进了确认态就收起发起表单
        _confirm_panel(executor, session_id, confirmation)
    else:
        from agent import pending_slots

        item = executor.submit(pending_slots.peek, session_id).result() or {}
        if item.get("intent") == "aa_collect":
            st.session_state["aa-create-open"] = True     # 正在补槽 → 直接展开
        if st.button("＋ 新增群收款" if not st.session_state.get("aa-create-open")
                     else "收起新增群收款",
                     key="aa-create-toggle", type="primary" if not st.session_state.get("aa-create-open")
                     else "secondary", width="stretch"):
            _toggle("aa-create-open")
        if st.session_state.get("aa-create-open"):
            _create_panel(executor, session_id)
    for item in executor.submit(aa_flow.active).result():
        _status_card(executor, session_id, item)
