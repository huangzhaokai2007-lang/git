"""F07 选卡、影响提示、验证码和显式提交；仅调用 agent。"""
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from agent import card_binding_flow, card_lock_flow as flow


def reset() -> None:
    for key in list(st.session_state):
        if str(key).startswith('lock-'):
            del st.session_state[key]


def _state(executor: ThreadPoolExecutor, session: str) -> dict:
    scope = (executor.submit(card_binding_flow.owner_key).result(), session)
    if st.session_state.get('lock-scope') != scope:
        reset()
        st.session_state['lock-scope'] = scope
    return st.session_state.setdefault('lock-view', {'open': False, 'preview': None, 'result': None, 'error': None})


def accept_turn(executor: ThreadPoolExecutor, session: str, ask: str | None) -> None:
    state = _state(executor, session)
    if ask == 'card_lock':
        state.update(open=True, preview=None, result=None, error=None)
    elif ask == 'lock_records':
        st.session_state['navigate-page'] = '💳 我的银行卡'
        st.session_state['limit-tab-request'] = '锁卡记录'


def _dismiss() -> None:
    state = st.session_state.get('lock-view', {})
    if preview := state.get('preview'):
        st.session_state['lock-cancel-request'] = preview['token']
    state['open'] = False


def _select(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    result = executor.submit(flow.cards).result()
    if not result['ok']:
        st.error(result['message'])
        return
    items = result['data']['items']
    st.write('请选择要临时锁定的已绑定银行卡')
    if not items:
        st.info('暂无已绑定银行卡，请先绑定模拟卡。')
    for item in items:
        st.caption(f"{item['card_no_mask']} · {card_binding_flow.STATUS_LABELS[item['status']]}")
        if st.button(f"锁定 {item['card_no_mask']}", disabled=item['status'] != 'normal',
                     key=f"lock-card-{item['card_id']}", width='stretch'):
            preview = executor.submit(flow.preview, item['card_id'], session).result()
            state['preview'] = preview['data'] if preview['ok'] else None
            state['error'] = None if preview['ok'] else preview['message']
            st.rerun()


def _confirm(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    preview = state['preview']
    st.write(f"即将锁定：{preview['card_no_mask']}")
    st.warning(preview['impact'])
    st.caption('请核对卡片；点击确认锁卡后才执行。解锁需另行走验证与复核流程。')
    with st.form('lock-confirm-form', clear_on_submit=True):
        otp = st.text_input('模拟短信验证码', type='password')
        submitted = st.form_submit_button('确认锁卡', type='primary')
    if submitted:
        result = executor.submit(flow.submit, preview['token'], otp, session).result()
        state['result'] = result if result['ok'] else None
        state['error'] = None if result['ok'] else result['message']
        st.rerun()


@st.dialog('临时锁定银行卡', width='large', on_dismiss=_dismiss)
def _dialog(executor: ThreadPoolExecutor, session: str) -> None:
    state = _state(executor, session)
    st.caption('模拟环境 · 卡片与验证码均为演示数据')
    if state['result']:
        st.success(state['result']['message'])
        if st.button('查看锁卡记录'):
            reset()
            accept_turn(executor, session, 'lock_records')
            st.rerun()
        if st.button('关闭'):
            reset()
            st.rerun()
        return
    if state['error']:
        st.error(state['error'])
    if state['preview']:
        _confirm(executor, session, state)
    else:
        _select(executor, session, state)
    if st.button('取消锁卡'):
        if state['preview']:
            executor.submit(flow.cancel, state['preview']['token'], session).result()
        reset()
        st.rerun()


def render(executor: ThreadPoolExecutor, session: str, *, show_button: bool = True) -> None:
    state = _state(executor, session)
    if token := st.session_state.pop('lock-cancel-request', None):
        executor.submit(flow.cancel, token, session).result()
    if show_button and st.button('临时锁卡', key='lock-open'):
        import card_limit_ui
        card_limit_ui.reset()
        state.update(open=True, preview=None, result=None, error=None)
    if state['open']:
        _dialog(executor, session)


def records(executor: ThreadPoolExecutor) -> None:
    st.markdown('### 锁卡记录')
    st.caption('最近的成功锁卡记录，最多展示一百条；重置会话不会删除记录。')
    result = executor.submit(flow.records).result()
    if not result['ok']:
        st.error(result['message'])
    elif not result['data']['items']:
        st.info('暂无锁卡记录。')
    else:
        st.dataframe(result['data']['items'], hide_index=True, width='stretch')
