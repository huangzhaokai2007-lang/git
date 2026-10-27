"""One live result; historical messages contain no interactive business views."""
import re

import streamlit as st

from agent import scene_flow, bill_detail_flow
from interfaces.web import spending_pie, insight_card_ui
import card_binding_ui, card_limit_ui, card_lock_ui, card_lost_ui
import payee_card_ui, schedule_card_ui, aa_card_ui, gift_card_ui
import activity_ui, payee_picker_ui, card_edit_ui, scene_ui

READS = {'balance_query', 'txn_query', 'bill_analysis', 'bill_report', 'anomaly_check',
         'card_query', 'payee_list', 'subscription_list', 'wealth_holdings', 'wealth_all_products'}


def family(turn: dict) -> str:
    intent = turn.get('intent') or ''
    if intent in {'card_query', 'card_lock', 'card_unlock', 'card_report_lost', 'card_limit_adjust'}:
        return 'cards'
    if intent in {'txn_query', 'bill_analysis', 'bill_report', 'anomaly_check'}:
        return 'bills'
    if intent.startswith('transfer_'):
        return 'transfer'
    return intent


def summary(message: dict) -> str:
    turn = message.get('turn') or {}
    if not turn.get('error_code'):
        if 'OTP' in turn.get('states', []):
            return '请在当前业务区提交短信验证码。'
        if 'CONFIRM_CARD' in turn.get('states', []):
            return '已生成确认信息，请在当前业务区核对后确认；尚未提交执行。'
    text = str(message.get('text') or turn.get('reply') or '')
    lines = [line.strip() for line in text.splitlines() if line.strip()
             and not line.lstrip().startswith(('|', '#', '<'))]
    return (lines[0][:180] if lines else '请查看当前业务区的处理结果。')


def accept(turn: dict, prompt: str) -> None:
    if turn.get('error_code'):
        st.session_state['workspace-error'] = {'turn': turn, 'prompt': prompt}
        return
    st.session_state.pop('workspace-error', None)
    st.session_state['operation-records-open'] = False
    if turn.get('ask') == 'scene_reset':
        reset()
        return
    previous = st.session_state.get('workspace-result') or {}
    if previous and previous.get('scene') != family(turn):
        st.session_state.pop('workspace-shortcut', None)
    st.session_state['workspace-result'] = {'turn': turn, 'prompt': prompt, 'scene': family(turn)}


def reset() -> None:
    for key in ('workspace-result', 'workspace-error', 'workspace-shortcut', 'active-scene'):
        st.session_state.pop(key, None)


def current() -> str:
    if st.session_state.get('operation-records-open'):
        return 'records'
    return st.session_state.get('workspace-result', {}).get('scene', '')


def reuse_shortcut(prompt: str) -> bool:
    return (prompt == st.session_state.get('workspace-shortcut')
            and bool(current() or st.session_state.get('workspace-error')))


def error(ask) -> None:
    failed = st.session_state.get('workspace-error')
    if not failed:
        return
    st.error(failed['turn'].get('reply') or '这次请求未能完成。')
    if failed['turn'].get('intent') in READS and not failed['turn'].get('executed'):
        st.button('重试查询', key='workspace-retry', on_click=ask, args=(failed['prompt'],))


def scope(executor, session: str, ask) -> None:
    context = executor.submit(scene_flow.context, session).result()
    request = context.get('read_request') or context.get('slots') or {}
    dates = [request[key] for key in ('date_from', 'date_to') if request.get(key)]
    parts = [' 至 '.join(dates)] if len(dates) == 2 else [context['period']] if context.get('period') else []
    if context.get('category'):
        parts.append(context['category'])
    card = next((row for row in context.get('candidates', {}).get('card', [])
                 if row.get('card_id') == context.get('card_id')), None)
    if card:
        parts.append(card.get('card_no_mask', ''))
    if not parts or current() not in ('bills', 'cards'):
        return
    with st.container(key='workspace-scope'):
        st.caption('当前范围：' + ' · '.join(parts))
        with st.popover('修改范围'):
            period = context.get('period') or ''
            month = st.text_input('月份（YYYY-MM）', value=period, key='scope-month-' + period)
            if st.button('查询该月', key='scope-month-submit'):
                if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
                    ask(f'{month[:4]}年{int(month[5:])}月账单总结')
                else:
                    st.error('请输入有效月份，例如 2026-08。')
            if context.get('category') and st.button('清除分类', key='scope-category-clear'):
                executor.submit(scene_flow.clear_category, session).result()
                st.session_state.get('workspace-result', {}).pop('prompt', None)
                ask('最近的交易')
            st.button('切换银行卡', key='scope-change-card', on_click=ask, args=('看看卡片信息',))


def transaction_result(turn: dict) -> bool:
    facts = turn.get('facts') or {}
    if turn.get('intent') != 'txn_query' or 'items' not in facts:
        return False
    rows = [{'时间': str(row.get('ts') or '').replace('T', ' '),
             '交易对手': row.get('counterparty') or '—', '分类': row.get('category') or '—',
             '金额（元）': row.get('amount_yuan') or '—'} for row in facts['items']]
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
    else:
        st.info('当前范围没有符合条件的交易。')
    return True


def result(executor, session: str, ask, detail_loader) -> None:
    value = st.session_state.get('workspace-result')
    if not value or current() == 'records':
        return
    turn = value['turn']
    scene = value['scene']
    if scene == 'bills':
        key = 'workspace-' + str(turn.get('trace_id'))
        facts = turn.get('facts') or {}
        overview = [(label, facts[field]) for label, field in (
            ('支出合计（元）', 'out_sum_yuan'), ('收入合计（元）', 'in_sum_yuan'),
            ('净额（元）', 'net_yuan')) if facts.get(field) is not None]
        if not overview and facts.get('total_yuan') is not None:
            overview = [('支出合计（元）', facts['total_yuan'])]
        if overview:
            for column, (label, amount) in zip(st.columns(len(overview)), overview):
                column.metric(label, str(amount))
        def category(source, selected):
            selected_result = executor.submit(scene_flow.select_bill_category, session, source, selected).result()
            if selected_result['ok']:
                st.session_state['workspace-scope-updated'] = True
            return selected_result
        drawn = spending_pie.render_reply(turn.get('reply', ''), turn, key=key,
                    animate=False, detail_loader=detail_loader, on_category=category, compact=True)
        if not drawn and not transaction_result(turn):
            st.markdown(turn.get('reply') or '当前范围没有查询结果。')
        if facts:
            actions = [('最大的一笔', '看看最大的一笔'), ('查看流水', '最近的交易')]
            if executor.submit(scene_flow.context, session).result().get('txn_id'):
                actions.insert(1, ('关联银行卡', '用的是哪张卡'))
            if turn['intent'] == 'anomaly_check':
                actions = [('查看流水', '最近的交易')]
            columns = st.columns(len(actions))
            for index, (label, prompt) in enumerate(actions):
                columns[index].button(label, key=f'workspace-next-{index}', on_click=ask, args=(prompt,), width='stretch')
            insight_card_ui._export(turn, turn['intent'], facts, key)
    elif scene not in ('cards', 'payee_list') and not any(
            state in turn.get('states', []) for state in ('CONFIRM_CARD', 'OTP')):
        st.markdown(turn.get('reply') or '当前没有查询结果。')


def panels(executor, session: str, ask, pending, forms, confirmation) -> None:
    scene = current()
    scope(executor, session, ask)
    error(ask)
    result(executor, session, ask,
           lambda source, category: executor.submit(bill_detail_flow.load, session, source, category).result())
    if st.session_state.pop('workspace-scope-updated', False):
        st.rerun()
    activity_ui.render(st.session_state['turns'])
    pending()
    payee_picker_ui.render(executor, session)
    card_edit_ui.render(executor, session)
    forms()
    if scene == 'cards':
        if st.session_state.get('bound-card-query'):
            card_binding_ui.render(executor, session, show_limit_button=False)
        else:
            for renderer in (card_limit_ui.render, card_lock_ui.render, card_lost_ui.render):
                renderer(executor, session, show_button=False)
    renderers = (('payee_list', 'payee', payee_card_ui.render),
                 ('scheduled_list', 'schedule', schedule_card_ui.render),
                 ('aa_list', 'aa', aa_card_ui.render),
                 ('gift_order_list', 'gift', gift_card_ui.render))
    for name, flag, renderer in renderers:
        if scene == name and st.session_state.get(f'bound-{flag}-query'):
            renderer(executor, session)
    if scene not in ('cards', 'records'):
        scene_ui.render(executor, session, ask, show_scope=False)
    confirmation()
