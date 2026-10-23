"""F07 选卡、影响提示、验证码和显式提交；仅调用 agent。"""
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from agent import card_binding_flow, card_lock_flow as flow

LOCK_NOTICE = '''**临时锁卡办理须知**

1. 适用于暂时找不到银行卡、希望先暂停使用的情况；仅本人已绑定且状态正常的卡可以办理。
2. 锁卡后该卡进入锁定状态，不能调整额度；不会冻结关联账户资金，不影响其他正常卡，也不自动取消已有订阅或独立账户转账。
3. 找到卡后，本人通过模拟短信验证码可自助解锁。挂失卡、风控冻结卡或来源不明的锁定不能通过此入口恢复。
4. 点击卡旁图标或选择银行卡仅打开预览；输入验证码并点击确认才会锁卡。关闭未提交的表单不改变卡状态。
5. 临时锁卡不办理补卡。确认卡片遗失或被盗且需要更换，请选择挂失并补卡；挂失生效后原卡不可自助恢复。
6. 此处只使用模拟卡片及模拟验证码，不发送真实短信，不收取真实费用。'''


def _notice(*, inline: bool = False) -> None:
    if inline:
        with st.expander('临时锁卡办理须知与影响', expanded=True):
            st.markdown(LOCK_NOTICE)
    else:
        st.markdown(LOCK_NOTICE)


def reset() -> None:
    for key in list(st.session_state):
        if str(key).startswith('lock-'):
            del st.session_state[key]


def _state(executor: ThreadPoolExecutor, session: str) -> dict:
    scope = (executor.submit(card_binding_flow.owner_key).result(), session)
    if st.session_state.get('lock-scope') != scope:
        reset()
        st.session_state['lock-scope'] = scope
    return st.session_state.setdefault('lock-view', {'open': False, 'preview': None, 'result': None, 'error': None,
                                                    'mode': 'lock', 'inline': False, 'target': None})


def accept_turn(executor: ThreadPoolExecutor, session: str, ask: str | None) -> None:
    state = _state(executor, session)
    if ask in ('card_lock', 'card_missing', 'card_unlock'):
        _cancel_preview(executor, session, state)
        state.update(open=True, preview=None, result=None, error=None,
                     mode={'card_lock': 'lock', 'card_missing': 'choice', 'card_unlock': 'unlock'}[ask], inline=False)
    elif ask == 'lock_records':
        st.session_state['navigate-page'] = '💳 我的银行卡'
        st.session_state['limit-tab-request'] = '锁卡记录'


def _cancel_preview(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    if state['preview'] and not state['result']:
        operation = flow.unlock_cancel if state['mode'] == 'unlock' else flow.cancel
        executor.submit(operation, state['preview']['token'], session).result()


def open_card(executor: ThreadPoolExecutor, session: str, card_id: str, mode: str = 'lock', *, inline: bool = False) -> None:
    """卡号旁的图标只创建预览，不执行锁卡。"""
    state = _state(executor, session)
    _cancel_preview(executor, session, state)
    operation = flow.unlock_preview if mode == 'unlock' else flow.preview
    preview = executor.submit(operation, card_id, session).result()
    state.update(open=True, preview=preview['data'] if preview['ok'] else None,
                 result=None, error=None if preview['ok'] else preview['message'], mode=mode,
                 inline=inline, target=card_id)


def row_confirmation(executor: ThreadPoolExecutor, session: str, card_id: str) -> None:
    state = _state(executor, session)
    if not state['open'] or not state['inline'] or state['target'] != card_id:
        return
    if state['preview']:
        unlocking = state['mode'] == 'unlock'
        if not unlocking:
            _notice(inline=True)
        with st.form(f'lock-inline-form-{card_id}', clear_on_submit=True, border=False, width=220):
            with st.container(horizontal=True, gap='small'):
                otp = st.text_input('模拟短信验证码', type='password', label_visibility='collapsed',
                                    placeholder='短信验证码', key=f'lock-inline-otp-{card_id}', width=115, max_chars=6)
                submitted = st.form_submit_button('确认解锁' if unlocking else '确认锁定', type='primary')
        if submitted:
            operation = flow.unlock_submit if unlocking else flow.submit
            result = executor.submit(operation, state['preview']['token'], otp, session).result()
            if result['ok']:
                state.update(open=False, preview=None, result=result, error=None)
                st.toast(result['message'])
            else:
                state['error'] = result['message']
            st.rerun()
    if state['error']:
        st.caption(state['error'])


def _select(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    unlocking = state['mode'] == 'unlock'
    result = executor.submit(flow.unlock_cards if unlocking else flow.cards).result()
    if not result['ok']:
        st.error(result['message'])
        return
    items = result['data']['items']
    st.write('请选择要自助解锁的已绑定银行卡' if unlocking else '请选择要临时锁定的已绑定银行卡')
    if not items:
        st.info('暂无已绑定银行卡，请先绑定模拟卡。')
    for item in items:
        st.caption(f"{item['card_no_mask']} · {card_binding_flow.STATUS_LABELS[item['status']]}")
        allowed = item.get('can_unlock', False) if unlocking else item['status'] == 'normal'
        if st.button(f"{'解锁' if unlocking else '锁定'} {item['card_no_mask']}", disabled=not allowed,
                     key=f"lock-card-{item['card_id']}", width='stretch'):
            open_card(executor, session, item['card_id'], state['mode'])
            st.rerun()


def _confirm(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    preview = state['preview']
    unlocking = state['mode'] == 'unlock'
    st.write(f"{'即将解锁' if unlocking else '即将锁定'}：{preview['card_no_mask']}")
    st.warning(preview['impact'])
    if not unlocking:
        _notice()
    st.caption('本人临时锁定可验证后自助解锁，无需人工复核；挂失、风控冻结不适用。')
    with st.form('lock-confirm-form', clear_on_submit=True):
        otp = st.text_input('模拟短信验证码', type='password')
        submitted = st.form_submit_button('确认解锁' if unlocking else '确认锁卡', type='primary')
    if submitted:
        operation = flow.unlock_submit if unlocking else flow.submit
        result = executor.submit(operation, preview['token'], otp, session).result()
        state['result'] = result if result['ok'] else None
        state['error'] = None if result['ok'] else result['message']
        st.rerun()


def _missing(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    st.write('先锁定保护卡片，找到后可验证身份自助解锁。')
    if st.button('临时锁定', type='primary'):
        state['mode'] = 'lock'
        st.rerun()
    if st.button('挂失说明'):
        import card_lost_ui
        state['open'] = False
        card_lost_ui.accept_turn(executor, session, 'card_lost')
        st.rerun()


def _panel(executor: ThreadPoolExecutor, session: str) -> None:
    state = _state(executor, session)
    st.markdown({'lock': '### 临时锁定银行卡', 'unlock': '### 自助解锁银行卡', 'choice': '### 找不到银行卡怎么办？'}[state['mode']])
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
    if state['mode'] == 'choice':
        _missing(executor, session, state)
    elif state['preview']:
        _confirm(executor, session, state)
    else:
        _select(executor, session, state)
    if st.button('取消解锁' if state['mode'] == 'unlock' else '取消锁卡'):
        _cancel_preview(executor, session, state)
        reset()
        st.rerun()


def render(executor: ThreadPoolExecutor, session: str, *, show_button: bool = True) -> None:
    state = _state(executor, session)
    if show_button and st.button('临时锁卡', key='lock-open'):
        import card_limit_ui
        card_limit_ui.reset()
        _cancel_preview(executor, session, state)
        state.update(open=True, preview=None, result=None, error=None, mode='lock', inline=False)
    if state['open'] and not state['inline']:
        with st.container(border=True, width=640):
            _panel(executor, session)


def records(executor: ThreadPoolExecutor) -> None:
    st.markdown('### 锁卡记录')
    st.caption('最近的成功锁卡与解锁记录，最多展示一百条；重置会话不会删除记录。')
    result = executor.submit(flow.records).result()
    if not result['ok']:
        st.error(result['message'])
    elif not result['data']['items']:
        st.info('暂无锁卡记录。')
    else:
        st.dataframe(result['data']['items'], hide_index=True, width='stretch')
