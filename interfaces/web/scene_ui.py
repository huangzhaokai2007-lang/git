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


def render(executor, session: str, ask, *, show_scope: bool = True) -> None:
    context = executor.submit(scene_flow.context, session).result()
    if not context.get('intent'):
        return
    with st.container(key='scene-context'):
        parts = [context[k] for k in ('period', 'category', 'counterparty') if context.get(k)]
        card = next((r for r in context.get('candidates', {}).get('card', [])
                     if r.get('card_id') == context.get('card_id')), None)
        if card:
            parts.append(card.get('card_no_mask', ''))
        if parts and show_scope:
            st.caption(' › '.join(parts))
        _selection(context, ask)


def _selection(context: dict, ask) -> None:
    domain = {'txn_query': 'txn', 'payee_list': 'payee', 'subscription_list': 'subscription',
              'wealth_holdings': 'product', 'wealth_all_products': 'product'}.get(context.get('intent'))
    rows = context.get('candidates', {}).get(domain, [])
    if not rows or (domain == 'txn' and context.get('txn_id')):
        return
    labels = [str(row.get('merchant') or row.get('name') or row.get('counterparty') or row.get('id', ''))
              + (' · ' + str(row['ts']) if row.get('ts') else '') for row in rows]
    index = st.selectbox('选择项目继续查看', range(len(rows)), format_func=lambda i: labels[i],
                        key='scene-candidate-' + domain)
    st.button('查看所选详情', key='scene-select-' + domain,
              on_click=ask, args=(f'第{index + 1}个',),
              kwargs={} if domain == 'txn' else
              {'display': _selection_description(domain, rows[index], labels[index])})


def _selection_description(domain: str, row: dict, label: str) -> str:
    if domain == 'txn':
        parts = [str(row.get('ts') or '').replace('T', ' '),
                 str(row.get('counterparty') or row.get('merchant') or '交易'),
                 str(row['amount_yuan']) + ' 元' if row.get('amount_yuan') is not None else '']
        return '查看交易：' + ' · '.join(part for part in parts if part)
    return '查看详情：' + label
