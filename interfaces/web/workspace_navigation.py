"""Read extensions retain their origin; returning restores state without replay."""
from copy import deepcopy
import streamlit as st
from agent import confirm_card, scene_flow, shortcut_flow
import agent.view_navigation as view_navigation

LABELS = {'anomaly_check': '异常检测', 'bill_report': '账单', 'bill_analysis': '账单分析',
          'txn_query': '流水明细', 'balance_query': '账户余额', 'card_query': '银行卡',
          'payee_list': '收款人', 'subscription_list': '订阅', 'subscription_remind': '订阅提醒',
          'wealth_recommend': '理财推荐', 'wealth_all_products': '全部理财产品',
          'wealth_holdings': '我的理财'}


def label(value: dict) -> str:
    return LABELS.get(value.get('turn', {}).get('intent'), '原页面') if value else '首页'


def visible_messages(messages):
    value = st.session_state.get('workspace-result') or {}
    override = st.session_state.get('workspace-view') or {}
    if override.get('trace') == value.get('turn', {}).get('trace_id') and override:
        return override['messages']
    if st.session_state.get('workspace-nav-stack'):
        return messages[-2:]
    return messages


def push(executor, session: str) -> bool:
    import workspace_ui
    value = st.session_state.get('workspace-result') or {}
    if value and value.get('turn', {}).get('intent') not in workspace_ui.READS:
        return False
    token = executor.submit(view_navigation.capture, session, allow_empty=not value).result()
    if not token:
        return False
    messages = st.session_state['messages']
    frame = {'token': token, 'state': workspace_ui.view_state(),
             'messages': deepcopy(visible_messages(messages)), 'message_count': len(messages)}
    st.session_state.setdefault('workspace-nav-stack', []).append(frame)
    return True


def clear() -> None:
    for key in ('workspace-nav-stack', 'workspace-view', 'workspace-back-error'):
        st.session_state.pop(key, None)


def back(executor, session: str) -> None:
    import workspace_ui
    frames = st.session_state.get('workspace-nav-stack') or []
    if not frames:
        return
    frame = frames[-1]
    if executor.submit(confirm_card.current, session).result():
        executor.submit(shortcut_flow.begin, session).result()
    if not executor.submit(view_navigation.restore, session, frame['token']).result():
        st.session_state['workspace-back-error'] = '原查询已过期，请重新查询。'
        return
    frames.pop()
    for key in (*workspace_ui.VIEW_KEYS, 'workspace-error', 'workspace-return', 'workspace-back-error'):
        st.session_state.pop(key, None)
    st.session_state.update(deepcopy(frame['state']))
    value = st.session_state.get('workspace-result') or {}
    st.session_state['workspace-view'] = {'trace': value.get('turn', {}).get('trace_id'),
        'messages': frame['messages'], 'count': frame['message_count']}
    st.session_state['clarify_round'] = 0
    st.session_state['pending_id'] = executor.submit(scene_flow.context, session).result().get('pending_id')


def render_origin(executor, session: str) -> None:
    frames = st.session_state.get('workspace-nav-stack') or []
    if not frames:
        return
    import legacy_bill_ui
    import interfaces.web.insight_card_ui as insight_card_ui
    import interfaces.web.wealth_ui as wealth_ui
    frame = frames[0]
    value = frame['state'].get('workspace-result') or {}
    turn = value.get('turn') or {}
    with st.expander('原页面 · ' + label(value), expanded=True):
        if value.get('scene') == 'bills':
            legacy_bill_ui.render(frame['messages'], executor, session, lambda *_: None, readonly=True)
        else:
            st.markdown(turn.get('reply') or '您好，请问我能为您提供什么帮助？')
            insight_card_ui.render(turn, key='origin-' + str(turn.get('trace_id')),
                                  on_ask=lambda *_: None, readonly=True)
            source = frame['state'].get('workspace-wealth-source')
            if source:
                wealth_ui.render_turn(source, ask=lambda *_: None, key_prefix='origin-wealth', readonly=True)
    st.button('返回' + label(frames[-1]['state'].get('workspace-result') or {}),
              key='workspace-back', on_click=back, args=(executor, session))
    if problem := st.session_state.get('workspace-back-error'):
        st.error(problem)


def history(messages) -> None:
    override = st.session_state.get('workspace-view') or {}
    if not st.session_state.get('workspace-nav-stack') and override:
        from interfaces.web import components
        with st.expander('扩展查看记录', expanded=False):
            components.transcript(messages[override['count']:], compact=True)
