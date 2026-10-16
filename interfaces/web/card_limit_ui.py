"""F06 前端：只收集输入、展示 agent 事实；不访问库、不做金额计算。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import card_binding_flow, card_limit_flow as flow

STATUS_FILTERS = {"全部状态": None, "审核中": "pending", "已通过并生效": "approved", "未通过": "rejected"}


def reset() -> None:
    for key in list(st.session_state):
        if str(key).startswith("limit-"):
            del st.session_state[key]


def _state(executor: ThreadPoolExecutor, session_id: str) -> dict:
    scope = (executor.submit(card_binding_flow.owner_key).result(), session_id)
    if st.session_state.get("limit-scope") != scope:
        reset()
        st.session_state["limit-scope"] = scope
    return st.session_state.setdefault("limit-view", {
        "open": False, "kind": None, "selected": None, "preview": None, "result": None, "error": None,
    })


def open_request(executor: ThreadPoolExecutor, session_id: str, text: str) -> None:
    state = _state(executor, session_id)
    state.update(open=True, kind=None, selected=None, preview=None, result=None, error=None)


def accept_turn(executor: ThreadPoolExecutor, session_id: str, text: str, ask: str | None) -> None:
    if ask == "limit_adjust":
        open_request(executor, session_id, text)
    elif ask == "limit_records":
        st.session_state["navigate-page"] = "💳 我的银行卡"
        st.session_state["limit-tab-request"] = "额度调整记录"


def _cancel() -> None:
    reset()


def _types(state: dict) -> None:
    st.write("您希望调整哪一种额度？")
    with st.container(horizontal=True, gap="small"):
        for label, kind in flow.KINDS.items():
            if st.button(label, key=f"limit-kind-{kind}"):
                state["kind"] = kind
                st.rerun()


def _cards(executor: ThreadPoolExecutor, state: dict) -> None:
    result = executor.submit(flow.cards, state["kind"]).result()
    if not result["ok"]:
        st.error(result["message"])
        return
    st.write("请选择已绑定银行卡")
    items = result["data"]["items"]
    if not items:
        st.info("暂无已绑定银行卡，请先在我的银行卡页面绑定模拟卡。")
    for item in items:
        st.caption(f"{item['card_no_mask']} · {'信用卡' if item['type'] == 'credit' else '储蓄卡'} · "
                   f"当前额度 {item['current_limit_yuan']} 元")
        if not item["eligible"]:
            st.caption(item["reason"])
        if st.button(f"选择 {item['card_no_mask']}", key=f"limit-card-{item['card_id']}",
                     disabled=not item["eligible"], width="stretch"):
            state["selected"] = item["card_id"]
            st.rerun()


def _application_form(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    result = executor.submit(flow.cards, state["kind"]).result()
    item = next((row for row in (result.get("data") or {}).get("items", [])
                 if row["card_id"] == state["selected"]), None)
    if not item or not item["eligible"]:
        st.error("该卡当前不可调整，请重新选择银行卡。")
        return
    st.write(f"银行卡：{item['card_no_mask']}")
    st.write(f"当前额度：{item['current_limit_yuan']} 元")
    st.caption(f"模拟申请金额须大于零，不超过 {item['max_limit_yuan']} 元。")
    if state["kind"] == "daily":
        st.caption("该卡关联账户共用日限额；转账仍受平台硬上限及现有累计支出口径约束。")
    with st.form("limit-application-form"):
        amount = st.text_input("申请调整后的额度（元）", key="limit-amount", placeholder="请输入最终目标额度")
        days = st.number_input("临时额度有效天数", min_value=1, max_value=90, value=30,
                               key="limit-days") if state["kind"] == "temporary" else None
        submitted = st.form_submit_button("核对申请")
    if submitted:
        result = executor.submit(flow.preview, state["selected"], state["kind"], amount, days, session_id).result()
        state["preview"] = result["data"] if result["ok"] else None
        state["error"] = None if result["ok"] else result["message"]
        st.rerun()


def _confirm(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    item = state["preview"]
    st.write(f"银行卡：{item['card_no_mask']}")
    st.write(f"调整类型：{item['kind_cn']}")
    st.write(f"当前额度：{item['old_limit_yuan']} 元 → 申请额度：{item['target_yuan']} 元")
    if item["expires_at"]:
        st.write(f"临时期限：{item['days']} 天；预计到期：{item['expires_at'][:16].replace('T', ' ')}")
        st.caption("通过后开始计时，到期恢复固定授信额度。")
    st.caption("权限档 L2；请核对后手动提交。这里只进行模拟审批。")
    with st.form("limit-confirm-form", clear_on_submit=True):
        otp = st.text_input("模拟短信验证码", type="password", key="limit-otp")
        submitted = st.form_submit_button("确认提交申请", type="primary")
    if submitted:
        result = executor.submit(flow.submit, item["token"], otp, session_id).result()
        if result["ok"]:
            state["result"], state["preview"], state["error"] = result["data"], None, None
        else:
            state["error"] = result["message"]
        st.rerun()


def _result(state: dict) -> None:
    item = state["result"]
    display = {"pending": st.info, "approved": st.success, "rejected": st.error}[item["status"]]
    display(f"{item['status_cn']}：{item['reason']}")
    st.write(f"申请编号：{item['id']}")
    st.write(f"申请额度：{item['target_yuan']} 元；核准额度：{item['approved_limit_yuan']} 元")
    if st.button("查看申请记录", key="limit-view-records", type="primary"):
        state["open"] = False
        st.session_state["navigate-page"] = "💳 我的银行卡"
        st.session_state["limit-tab-request"] = "额度调整记录"
        st.rerun()


def _panel(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    st.markdown("### 额度调整申请")
    st.caption("模拟环境 · 所有卡片、审批及验证码均为演示数据")
    if state["error"]:
        st.error(state["error"])
    if state["result"]:
        _result(state)
    elif state["preview"]:
        _confirm(executor, session_id, state)
    elif state["selected"]:
        _application_form(executor, session_id, state)
    elif state["kind"]:
        _cards(executor, state)
    else:
        _types(state)
    if state["kind"] and not state["result"] and st.button("返回选择调整类型", key="limit-back"):
        state.update(kind=None, selected=None, preview=None, error=None)
        st.session_state.pop("limit-amount", None)
        st.rerun()
    if st.button("关闭" if state["result"] else "取消申请", key="limit-cancel"):
        _cancel()
        st.rerun()


def render(executor: ThreadPoolExecutor, session_id: str, *, show_button: bool = True) -> None:
    state = _state(executor, session_id)
    if show_button and st.button("调整额度", key="limit-open"):
        state.update(open=True, kind=None, selected=None, preview=None, result=None, error=None)
        st.session_state.pop("limit-amount", None)
    if state["open"]:
        with st.container(border=True, width=640, gap="small", key="credit-limit-panel"):
            _panel(executor, session_id, state)


def records(executor: ThreadPoolExecutor) -> None:
    st.markdown("### 额度调整记录")
    result = executor.submit(flow.records).result()
    if not result["ok"]:
        st.error(result["message"])
        return
    items = result["data"]["items"]
    if not items:
        st.info("暂无额度调整申请记录。提交申请后，可在这里查看三种审批状态。")
        return
    status = st.selectbox("申请状态", list(STATUS_FILTERS), key="limit-record-status")
    kinds = st.selectbox("调整类型", ["全部类型", *flow.KINDS], key="limit-record-kind")
    cards = st.selectbox("银行卡", ["全部卡片", *dict.fromkeys(item["card_no_mask"] for item in items)], key="limit-record-card")
    dates = st.date_input("申请日期范围", value=(), key="limit-record-date", help="留空显示全部日期，选择起止日期后筛选。")
    filtered = [item for item in items if (STATUS_FILTERS[status] is None or item["status"] == STATUS_FILTERS[status])
                and (kinds == "全部类型" or item["kind"] == flow.KINDS[kinds])
                and (cards == "全部卡片" or item["card_no_mask"] == cards)
                and (len(dates) != 2 or dates[0].isoformat() <= item["created_at"][:10] <= dates[1].isoformat())]
    st.dataframe([{"申请时间": i["created_at"][:19].replace("T", " "), "银行卡": i["card_no_mask"],
                   "类型": i["kind_cn"], "原额度（元）": i["old_limit_yuan"], "申请额度（元）": i["target_yuan"],
                   "核准额度（元）": i["approved_limit_yuan"], "状态": i["status_cn"]} for i in filtered],
                 hide_index=True, width="stretch")
    for item in filtered:
        with st.expander(f"申请详情 · {item['card_no_mask']} · {item['status_cn']} · {item['id']}"):
            st.write(item["reason"])
            st.write(f"生效时间：{item['effective_at'] or '尚未生效'}")
            if item["expires_at"]:
                st.write(f"{'申请期限截止' if item['status'] != 'approved' else '到期时间'}：{item['expires_at']}")
                if item["expired"]:
                    st.info("此笔临时申请已到期；当前生效额度以银行卡额度查询为准。")
            st.caption(f"审计追踪：{item['trace_id']}")
