"""群收款卡面（2026-10-28 重做）：一张卡里完成「填总额 → 选人 → 按人改金额 → 发起」，
另有「付款状态」卡。

用户原话：「把群收款整个卡面给修改一下…卡面内有总金额，点击选项后可以输入金额、付款人，
点击后会列出所有收款人的名字，可以上下滑动和多选，然后选择后还有一个确认按钮，
没选择时确认按钮不可按，然后设置一个按钮，点击按钮后可以像上面那样拉开，
拉开后可以设置不同人的不同收款金额，然后还要有一个查看付款人是否付款的卡片」
并且「群收款是要包含我的」（我和王五收 300 → 王五给我 150，我承担 150）。

分层：只调 `agent/`（`aa_flow` 出方案与付款状态、`write_flow` 走写流程），不碰 `tools/`。
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
    """按钮直达：清在途卡 → `write_flow.start` → 记回执。"""
    confirm_card.clear(session_id)
    _record(executor.submit(write_flow.start, intent, slots, session_id).result(), display)
    st.rerun()


def _toggle(key: str) -> None:
    st.session_state[key] = not st.session_state.get(key, False)


def _create_card(executor: ThreadPoolExecutor, session_id: str) -> None:
    """发起卡：总金额 → 选人（多选 + 确认）→ 按人金额（拉开）→ 发起。"""
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款（含你自己）",
                             "先填总额，再选参与人；每人均摊，你承担自己那一份", tone="")
        total = st.number_input("群收款总金额（元）", min_value=0.0, step=10.0, key="aa-total",
                                format="%.2f")

        if st.button("选择收款人" + ("  ▾" if st.session_state.get("aa-pick-open") else "  ▸"),
                     key="aa-pick-toggle", width="stretch"):
            _toggle("aa-pick-open")
        if st.session_state.get("aa-pick-open"):
            names = executor.submit(aa_flow.candidates).result()
            picked = st.multiselect("点击选择参与人（可多选、可上下滑动）", names,
                                    key="aa-picked", placeholder="还没有选择任何收款人")
            st.button("确认", key="aa-pick-confirm", type="primary",
                      disabled=not picked,                     # 没选择 → 不可按
                      on_click=_remember_people, args=(picked,))

        people = list(st.session_state.get("aa-people") or [])
        if not people:
            st.caption("还没有确认参与人 —— 选好后点「确认」。")
            return
        plan = executor.submit(aa_flow.group_plan, people, float(total or 0)).result()
        st.caption(f"{plan['count']} 人（含我）· 每人 {plan['per_yuan']} 元 · "
                   f"我承担 {plan['mine_yuan']} 元 · 待收 {plan['collect_yuan']} 元")

        if st.button("按人设置金额" + ("  ▾" if st.session_state.get("aa-split-open") else "  ▸"),
                     key="aa-split-toggle", width="stretch"):
            _toggle("aa-split-open")
        amounts: dict[str, str] = {}
        if st.session_state.get("aa-split-open"):
            st.caption("只填**其他人**该给你的金额；你自己那份由总额自动扣。")
            for index, name in enumerate(people):
                amounts[name] = st.text_input(f"{name} 应收（元）", key=f"aa-amt-{index}",
                                              value=plan["per_yuan"])

        if st.button("发起群收款", key="aa-create-go", type="primary", width="stretch"):
            slots = {"payee_ids": people, "amount": plan["collect_yuan"]}
            filled = {name: value.strip() for name, value in amounts.items() if value.strip()}
            if filled:
                slots["amounts"] = "、".join(f"{name} {value}" for name, value in filled.items())
            _start(executor, session_id, "aa_collect", slots,
                   "（按钮）发起群收款：" + "、".join(people))


def _remember_people(picked: list[str]) -> None:
    """「确认」回调：把选中的参与人记进会话（没选时按钮本来就不可按）。"""
    st.session_state["aa-people"] = list(picked)
    st.session_state["aa-pick-open"] = False


def _status_card(executor: ThreadPoolExecutor, session_id: str, item: dict) -> None:
    """付款状态卡：谁已付 / 谁没付 + 登记已付 + 撤销。"""
    request_id = str(item["request_id"])
    with st.container(border=True, key=f"aa-status-{request_id}"):
        premium_ui.card_head(f"🧾 {item['title']}",
                             f"{item['total_yuan']} 元 · 已付 {item['paid_count']}/"
                             f"{item['share_count']} 人 · 截止 {item['expire_at']}", tone="ok")
        shares = list(item.get("shares") or [])
        for share in shares:
            done = share["status"] == "paid"
            st.markdown(f"{'✅' if done else '⏳'} **{share['payee_name']}** · "
                        f"{share['amount_yuan']} 元 · {'已付' if done else '未付'}")
        pending = [share for share in shares if share["status"] != "paid"]
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
    """一张发起卡 + 每笔一条付款状态卡（不再拆成多块）。"""
    _create_card(executor, session_id)
    items = executor.submit(aa_flow.active).result()
    for item in items:
        _status_card(executor, session_id, item)
