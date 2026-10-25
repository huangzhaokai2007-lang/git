"""Small contextual controls; all selection validation and facts belong to agent."""
import streamlit as st

from agent import scene_flow


def accept(executor, session: str, turn) -> None:
    import card_binding_ui, card_lock_ui, card_lost_ui, card_limit_ui
    if turn.ask == 'scene_reset':
        state = st.session_state.get('lock-view')
        if state:
            card_lock_ui._cancel_preview(executor, session, state)
        card_lock_ui.reset()
        card_lost_ui.reset()
        card_limit_ui.reset()
        card_binding_ui.reset()
        st.session_state['bound-card-query'] = False
        return
    context = executor.submit(scene_flow.context, session).result()
    card_id = context.get('card_id')
    if turn.intent == 'card_query' and card_id:
        state = card_binding_ui._state(executor, session)
        state['selected'] = card_id
    if card_id and turn.ask in ('card_lock', 'card_unlock'):
        card_lock_ui.open_card(executor, session, card_id,
                               'unlock' if turn.ask == 'card_unlock' else 'lock', inline=True)
        st.session_state['bound-card-query'] = True


def render(executor, session: str, ask) -> None:
    context = executor.submit(scene_flow.context, session).result()
    if not context.get('intent'):
        return
    with st.container(key='scene-context'):
        parts = [context[k] for k in ('period', 'category', 'counterparty') if context.get(k)]
        card = next((r for r in context.get('candidates', {}).get('card', [])
                     if r.get('card_id') == context.get('card_id')), None)
        if card:
            parts.append(card.get('card_no_mask', ''))
        if parts:
            st.caption(' › '.join(parts))
        columns = st.columns(2)
        columns[0].button('返回上次查询', key='scene-back', on_click=ask, args=('返回刚才的列表',))
        columns[1].button('清除查询条件', key='scene-clear', on_click=ask, args=('重新来',))
        _selection(context, ask)


def _selection(context: dict, ask) -> None:
    domain = {'txn_query': 'txn', 'payee_list': 'payee', 'subscription_list': 'subscription',
              'wealth_holdings': 'product', 'wealth_all_products': 'product'}.get(context.get('intent'))
    rows = context.get('candidates', {}).get(domain, [])
    if not rows:
        return
    labels = [str(row.get('merchant') or row.get('name') or row.get('counterparty') or row.get('id', ''))
              + (' · ' + str(row['ts']) if row.get('ts') else '') for row in rows]
    index = st.selectbox('选择项目继续查看', range(len(rows)), format_func=lambda i: labels[i],
                        key='scene-candidate-' + domain)
    st.button('查看所选详情', key='scene-select-' + domain,
              on_click=ask, args=(f'第{index + 1}个',))
