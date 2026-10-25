"""模拟绑卡与已绑定卡展示；仅调用 agent，不处理金额或校验密码。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import card_binding_flow as flow, scene_flow
import card_limit_ui as limits_ui
import card_lock_ui as locks_ui
import card_gallery_ui as gallery_ui

FILTERS = {"全部": None, "正常": "normal", "已锁定": "locked", "已挂失": "lost", "已冻结": "frozen"}


def reset(turn=None) -> None:
    for key in list(st.session_state):
        if str(key).startswith(("cards-", "bind-", "reveal-", "balance-", "detail-")):
            del st.session_state[key]
    if turn is not None and turn.ask == flow.BINDING_PROMPT:
        st.session_state["bind-open-request"] = True


def _state(executor: ThreadPoolExecutor, session_id: str) -> dict:
    requested_form = st.session_state.pop("bind-open-request", False)
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
        "form": requested_form, "preview": None, "notice": None, "visible": {},
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
    state['detail_revision'] = state.get('detail_revision', 0) + 1
    result = executor.submit(scene_flow.select_card, st.session_state["session_id"], card_id).result()
    if not result["ok"]:
        state["notice"] = (False, result["message"])
    else:
        locks_ui.close(executor, st.session_state["session_id"])
        state["selected"] = card_id
        state["full_number"], state["reveal_form"], state["detail_balance"] = None, False, False
        messages = st.session_state.setdefault("messages", [])
        messages.append({"role": "assistant", "text": result["summary"], "turn": None, "binding_detail": True})
    st.rerun()


def _row(executor: ThreadPoolExecutor, session_id: str, item: dict, state: dict) -> None:
    card_id = item["card_id"]
    if gallery_ui.face(item):
        _select(executor, card_id, state)
    st.caption('点击卡片查看详情')
    columns = st.columns([1, 2], vertical_alignment='center')
    with columns[0].container(horizontal=True, vertical_alignment='center'):
        st.caption(item['status_cn'])
        normal = item['status'] == 'normal'
        locked = item['status'] == 'locked'
        unlockable = locked and item.get('self_unlockable', False)
        hint = '临时锁定此卡' if normal else '短信验证后自助解锁' if unlockable else '锁定来源无法确认，不能自助解锁' if locked else f"{item['status_cn']}，不能自助解锁"
        if st.button('', key=f'lock-toggle-{card_id}', disabled=not (normal or unlockable),
                       icon=':material/lock_open:' if normal else ':material/lock:', help=hint):
            locks_ui.open_card(executor, session_id, card_id, 'unlock' if locked else 'lock', inline=True)
            st.rerun()
    visible = state["visible"].get(card_id, False)
    with columns[1]:
        amount, button = st.columns([3, 2])
        amount.text(f"{item['balance_yuan']} 元" if visible else "****** 元")
        if button.button("隐藏" if visible else "显示", key=f"balance-{card_id}",
                         icon=":material/visibility:" if visible else ":material/visibility_off:",
                         help="隐藏余额" if visible else "显示余额"):
            state["visible"][card_id] = not visible
            st.rerun()
    locks_ui.row_confirmation(executor, session_id, card_id)


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
    st.text(f"{result['data']['balance_yuan']} 元" if visible else "****** 元")
    if st.button("隐藏详情余额" if visible else "显示详情余额", key="detail-balance"):
        state["detail_balance"] = not visible
        st.rerun()
    _reveal(executor, session_id, state)
    if st.button("关闭详情", key="detail-close"):
        state['detail_revision'] = state.get('detail_revision', 0) + 1
        state["selected"], state["full_number"], state["reveal_form"] = None, None, False
        st.rerun()


def _render_cards(executor: ThreadPoolExecutor, session_id: str) -> None:
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
    selected_filter = st.selectbox("卡片状态", list(FILTERS), key="cards-filter", label_visibility="collapsed")
    if selected_filter != state["last_filter"]:
        state["visible"], state["full_number"] = {}, None
        state['selected'], state['reveal_form'], state['detail_balance'] = None, False, False
        state["last_filter"] = selected_filter
    result = executor.submit(flow.list_cards, FILTERS[selected_filter]).result()
    if not result["ok"]:
        st.error(result["message"])
        return
    items = result["data"]["items"]
    if not items:
        st.info("您还未绑定银行卡。" if selected_filter == "全部" else "没有该状态的已绑定银行卡。")
    else:
        gallery_ui.deck(items, state['selected'], lambda item: _row(executor, session_id, item, state), state.get('detail_revision', 0))
    with st.container(key='bank-gallery-details'):
        _details(executor, session_id, state)


def render(executor: ThreadPoolExecutor, session_id: str, *, show_limit_button: bool = True) -> None:
    if requested := st.session_state.pop("limit-tab-request", None):
        st.session_state["cards-record-view"] = requested
    import card_lost_ui
    record_view = st.session_state.get("cards-record-view")
    if record_view:
        if st.button("返回我的银行卡", key="cards-record-back"):
            st.session_state.pop("cards-record-view", None)
            st.rerun()
    if record_view == "额度调整记录":
        limits_ui.records(executor)
    elif record_view == "锁卡记录":
        locks_ui.records(executor)
    elif record_view == "挂失补卡记录":
        card_lost_ui.records(executor, session_id, prefix='tab')
    else:
        _render_cards(executor, session_id)
    limits_ui.render(executor, session_id, show_button=show_limit_button)
    locks_ui.render(executor, session_id)
    card_lost_ui.render(executor, session_id)
