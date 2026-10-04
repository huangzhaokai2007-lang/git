"""模拟绑卡与已绑定卡展示；仅调用 agent，不处理金额或校验密码。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import card_binding_flow as flow

FILTERS = {"全部": None, "正常": "normal", "已锁定": "locked", "已挂失": "lost", "已冻结": "frozen"}


def reset() -> None:
    for key in list(st.session_state):
        if str(key).startswith(("cards-", "bind-", "reveal-", "balance-", "detail-")):
            del st.session_state[key]


def _state(executor: ThreadPoolExecutor, session_id: str) -> dict:
    owner = executor.submit(flow.owner_key).result()
    scope = (owner, session_id)
    if st.session_state.get("cards-scope") != scope:
        previous = st.session_state.get("cards-scope")
        reset()
        st.session_state["cards-scope"] = scope
        if previous and previous[0] != owner:
            messages = st.session_state.get("messages", [])
            st.session_state["messages"] = [message for message in messages if not _card_message(message)]
            st.session_state["turns"] = [turn for turn in st.session_state.get("turns", []) if not _card_message(turn)]
            st.session_state["bound-card-query"] = False
    return st.session_state.setdefault("cards-view", {
        "form": False, "preview": None, "notice": None, "visible": {},
        "selected": None, "reveal_form": False, "full_number": None,
        "detail_balance": False, "last_filter": "全部",
    })


def prepare(executor: ThreadPoolExecutor, session_id: str) -> None:
    """在聊天历史渲染前清理切换用户遗留的卡片详情。"""
    _state(executor, session_id)


def _card_message(message: dict) -> bool:
    return bool(message.get("binding_detail") or (message.get("turn") or {}).get("intent") == "card_query")


def _form(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    if not state["form"]:
        return
    preview = state["preview"]
    if preview:
        st.info(f"已验证 {preview['card_no_mask']}，确认后加入我的银行卡。")
        if st.button("确认绑定", key="bind-confirm", type="primary"):
            result = executor.submit(flow.confirm, preview["token"], session_id).result()
            state["notice"] = (result["ok"], result["message"])
            state["preview"], state["form"], state["visible"] = None, not result["ok"], {}
            st.rerun()
    else:
        with st.form("bind-form", clear_on_submit=True):
            number = st.text_input("模拟银行卡号", key="bind-number")
            password = st.text_input("银行卡密码", type="password", key="bind-password")
            submitted = st.form_submit_button("验证银行卡")
        if submitted:
            result = executor.submit(flow.preview, number, password, session_id).result()
            state["preview"] = result["data"] if result["ok"] else None
            state["notice"] = (result["ok"], result["message"])
            st.rerun()
    if st.button("取消绑定", key="bind-cancel"):
        state["form"], state["preview"], state["notice"] = False, None, None
        st.session_state.pop("bind-password", None)
        st.session_state.pop("bind-number", None)
        st.rerun()


def _select(executor: ThreadPoolExecutor, card_id: str, state: dict) -> None:
    result = executor.submit(flow.detail, card_id).result()
    if not result["ok"]:
        state["notice"] = (False, result["message"])
    else:
        state["selected"] = card_id
        state["full_number"], state["reveal_form"], state["detail_balance"] = None, False, False
        messages = st.session_state.setdefault("messages", [])
        messages.append({"role": "assistant", "text": result["summary"], "turn": None, "binding_detail": True})
    st.rerun()


def _row(executor: ThreadPoolExecutor, item: dict, state: dict) -> None:
    card_id = item["card_id"]
    columns = st.columns([3, 1.5, 2.5])
    if columns[0].button(item["card_no_mask"], key=f"detail-{card_id}"):
        _select(executor, card_id, state)
    columns[1].markdown(item["status_cn"])
    visible = state["visible"].get(card_id, False)
    with columns[2]:
        amount, button = st.columns([3, 2])
        amount.markdown(f"{item['balance_yuan']} 元" if visible else "******")
        if button.button("隐藏" if visible else "显示", key=f"balance-{card_id}",
                         icon=":material/visibility:" if visible else ":material/visibility_off:",
                         help="隐藏余额" if visible else "显示余额"):
            state["visible"][card_id] = not visible
            st.rerun()


def _reveal(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    if state["full_number"]:
        st.markdown(f"完整模拟卡号：`{state['full_number']}`")
        if st.button("收起完整卡号", key="reveal-close"):
            state["full_number"] = None
            st.rerun()
    elif state["reveal_form"]:
        with st.form("reveal-form", clear_on_submit=True):
            password = st.text_input("请输入此卡的银行卡密码", type="password", key="reveal-password")
            submitted = st.form_submit_button("验证并查看")
        if submitted:
            result = executor.submit(flow.reveal, state["selected"], password, session_id).result()
            if result["ok"]:
                state["full_number"], state["reveal_form"] = result["data"]["card_no"], False
                state["notice"] = None
            else:
                state["notice"] = (False, result["message"])
            st.rerun()
        if st.button("取消查看", key="reveal-cancel"):
            state["reveal_form"] = False
            st.session_state.pop("reveal-password", None)
            st.rerun()
    elif st.button("查看完整卡号", key="reveal-open"):
        state["reveal_form"] = True
        st.rerun()


def _details(executor: ThreadPoolExecutor, session_id: str, state: dict) -> None:
    if not state["selected"]:
        return
    result = executor.submit(flow.detail, state["selected"]).result()
    if not result["ok"]:
        state["selected"], state["full_number"] = None, None
        st.error(result["message"])
        return
    st.divider()
    st.markdown("### 银行卡详情")
    st.markdown(result["summary"])
    st.caption("此卡关联账户余额；信用卡余额与授信额度分别展示。")
    visible = state["detail_balance"]
    st.markdown(f"{result['data']['balance_yuan']} 元" if visible else "******")
    if st.button("隐藏详情余额" if visible else "显示详情余额", key="detail-balance"):
        state["detail_balance"] = not visible
        st.rerun()
    _reveal(executor, session_id, state)
    if st.button("关闭详情", key="detail-close"):
        state["selected"], state["full_number"], state["reveal_form"] = None, None, False
        st.rerun()


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    state = _state(executor, session_id)
    st.markdown("### 我的银行卡")
    st.caption("仅展示已绑定的模拟银行卡。")
    if st.button("绑定银行卡", key="bind-open"):
        state["form"], state["preview"], state["notice"] = True, None, None
        state["full_number"] = None
        st.rerun()
    if state["notice"]:
        ok, message = state["notice"]
        (st.success if ok else st.error)(message)
    _form(executor, session_id, state)
    selected_filter = st.selectbox("卡片状态", list(FILTERS), key="cards-filter")
    if selected_filter != state["last_filter"]:
        state["visible"], state["full_number"] = {}, None
        state["last_filter"] = selected_filter
    result = executor.submit(flow.list_cards, FILTERS[selected_filter]).result()
    if not result["ok"]:
        st.error(result["message"])
        return
    items = result["data"]["items"]
    if not items:
        st.info("您还未绑定银行卡。" if selected_filter == "全部" else "没有该状态的已绑定银行卡。")
    else:
        columns = st.columns([3, 1.5, 2.5])
        for column, label in zip(columns, ("卡号", "状态", "余额")):
            column.markdown(f"**{label}**")
        for item in items:
            _row(executor, item, state)
    _details(executor, session_id, state)
