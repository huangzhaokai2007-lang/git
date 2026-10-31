"""One live result; historical messages contain no interactive business views."""
import re
from copy import deepcopy

import streamlit as st

from agent import scene_flow, bill_detail_flow, subscription_notice, wealth_daily
import agent.view_navigation as view_navigation
from interfaces.web import wealth_ui, risk_ui
import interfaces.web.subscription_ui as subscription_ui
import card_binding_ui, card_limit_ui, card_lock_ui, card_lost_ui
import payee_card_ui, schedule_card_ui, aa_card_ui, gift_card_ui
import activity_ui, payee_picker_ui, card_edit_ui, scene_ui, slot_form_ui, legacy_bill_ui
import workspace_navigation as navigation

READS = {'balance_query', 'txn_query', 'bill_analysis', 'bill_report', 'anomaly_check',
         'card_query', 'payee_list', 'subscription_list', 'subscription_remind',
         'wealth_holdings', 'wealth_all_products', 'wealth_recommend', 'risk_assess'}
VIEW_KEYS = ('workspace-result', 'workspace-shortcut', 'cards-view', 'operation-records-open',
             'workspace-wealth-source', 'active-scene',
             *(f'bound-{domain}-query' for domain in ('card', 'payee', 'schedule', 'aa', 'gift', 'lost')))


def view_state() -> dict:
    return deepcopy({key: st.session_state[key] for key in VIEW_KEYS if key in st.session_state})


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
    st.session_state.pop('workspace-view', None)
    st.session_state['operation-records-open'] = False
    if turn.get('ask') == 'scene_reset':
        reset()
        return
    if turn.get('intent') in READS or turn.get('executed'):
        st.session_state.pop('workspace-return', None)
    if turn.get('intent') == 'wealth_recommend' and (turn.get('facts') or {}).get('items'):
        st.session_state['workspace-wealth-source'] = turn
    elif turn.get('intent') not in ('wealth_all_products', 'wealth_recommend'):
        st.session_state.pop('workspace-wealth-source', None)
    previous = st.session_state.get('workspace-result') or {}
    if previous and previous.get('scene') != family(turn):
        st.session_state.pop('workspace-shortcut', None)
    st.session_state['workspace-result'] = {'turn': turn, 'prompt': prompt, 'scene': family(turn)}


def reset() -> None:
    navigation.clear()
    for key in ('workspace-result', 'workspace-error', 'workspace-shortcut', 'active-scene',
                'workspace-return', 'workspace-wealth-source'):
        st.session_state.pop(key, None)


def remember_view(executor, session: str) -> None:
    value = st.session_state.get('workspace-result') or {}
    if value.get('turn', {}).get('intent') not in READS:
        return
    token = executor.submit(view_navigation.capture, session).result()
    if token:
        st.session_state['workspace-return'] = {'token': token, 'state': view_state()}


def return_after_cancel(executor, session: str) -> None:
    saved = st.session_state.pop('workspace-return', None)
    restored = saved and executor.submit(view_navigation.restore, session, saved['token']).result()
    for key in ('workspace-result', 'workspace-error', 'workspace-shortcut'):
        st.session_state.pop(key, None)
    st.session_state['pending_id'] = None
    st.session_state['clarify_round'] = 0
    if restored:
        st.session_state.update(saved['state'])


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
    #: `wealth_holdings` 也算：看持仓详情同样要求先测评（T21/T26），
    #: 只认 `wealth_recommend` 的话这条路径只有一行报错、没有按钮（用户实测反馈）。
    if failed['turn'].get('intent') in ('wealth_recommend', 'wealth_holdings'):
        wealth_ui.render_turn(failed['turn'], ask=ask, key_prefix='workspace-wealth-error')
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


def result(executor, session: str, ask, detail_loader) -> None:
    value = st.session_state.get('workspace-result')
    if not value or current() == 'records':
        return
    turn = value['turn']
    scene = value['scene']
    if scene == 'bills':
        return
    elif scene == 'wealth_recommend':
        if (turn.get('facts') or {}).get('items'):
            wealth_ui.render_turn(turn, ask=ask, key_prefix='workspace-wealth-' + str(turn.get('trace_id')),
                daily_loader=lambda pid, gran='daily': executor.submit(
                    wealth_daily.load, session, pid, gran).result())
        else:
            st.info(turn.get('reply') or '当前没有可推荐的产品。')
    elif scene == 'wealth_holdings':
        #: 持仓页和推荐页同一套卡片：金额大字 + K 线 + 还能带来多少 + 赎回。
        #: **回执原文不再上屏** —— 卡片已经把名称/金额/年化/买入日列全，再打一遍就是两份数据。
        wealth_ui.render_holdings(
            turn, ask=ask, key_prefix='workspace-hold-' + str(turn.get('trace_id')),
            daily_loader=lambda pid, gran='monthly': executor.submit(
                wealth_daily.load, session, pid, gran).result())
    elif scene == 'risk_assess':
        st.markdown(turn.get('reply') or '当前没有查询结果。')
        risk_ui.render_turn(turn, ask=ask, key_prefix='workspace-risk-' + str(turn.get('trace_id')))
    elif scene == 'wealth_all_products':
        st.markdown(turn.get('reply') or '当前没有查询结果。')
        if source := st.session_state.get('workspace-wealth-source'):
            wealth_ui.render_turn(source, ask=ask, key_prefix='workspace-wealth-' + str(source.get('trace_id')),
                daily_loader=lambda pid, gran='daily': executor.submit(
                    wealth_daily.load, session, pid, gran).result())
    elif (scene in ('subscription_list', 'subscription_remind')
          and (turn.get('facts') or {}).get('status', 'active') == 'active'
          and ((turn.get('facts') or {}).get('items') or (turn.get('facts') or {}).get('overdue_items'))):
        subscription_ui.render_turn(turn, ask=ask, key_prefix='workspace-subs-' + str(turn.get('trace_id')))
    elif scene not in ('cards', 'payee_list') and not any(
            state in turn.get('states', []) for state in ('CONFIRM_CARD', 'OTP')):
        st.markdown(turn.get('reply') or '当前没有查询结果。')


def notifications(executor, session: str, ask) -> None:
    subscription_ui.banner(executor.submit(subscription_notice.summary, session).result(), ask=ask)


def transcript(messages, executor, session: str, ask) -> None:
    navigation.render_origin(executor, session)
    navigation.history(messages)
    messages = navigation.visible_messages(messages)
    if st.session_state.get('workspace-error') and st.session_state.get('workspace-nav-stack'):
        if current() == 'bills':
            error(ask)
        return
    if current() == 'bills':
        legacy_bill_ui.render(messages, executor, session, ask)
        error(ask)
        scene_ui.render(executor, session, ask)
        return
    from interfaces.web import components
    if len(messages) > 4:
        with st.expander('较早的聊天记录', expanded=False):
            components.transcript(messages[:-4], compact=True)
    components.transcript(messages[-4:], compact=True)


def panels(executor, session: str, ask, pending, forms, confirmation) -> None:
    scene = current()
    if scene != 'bills':
        scope(executor, session, ask)
    error(ask)
    if st.session_state.get('workspace-error') and st.session_state.get('workspace-nav-stack'):
        return
    result(executor, session, ask,
           lambda source, category: executor.submit(bill_detail_flow.load, session, source, category).result())
    if st.session_state.pop('workspace-scope-updated', False):
        st.rerun()
    activity_ui.render(st.session_state['turns'])
    pending()
    payee_picker_ui.render(executor, session)
    #: 2026-10-30：修改面板已作为**确认卡内部抽屉**渲染（见 components.confirm_card 的 edit_panel），
    #: 这里不再单独画，否则同一轮会出现两个 card-edit-box（Streamlit 会直接报重复 key）。
    forms()
    if scene == 'cards':
        if st.session_state.get('bound-card-query'):
            card_binding_ui.render(executor, session, show_limit_button=False)
        else:
            for renderer in (card_limit_ui.render, card_lock_ui.render, card_lost_ui.render):
                renderer(executor, session, show_button=False)
    inline_confirmation_rendered = False

    def inline_confirmation(**kwargs):
        nonlocal inline_confirmation_rendered
        inline_confirmation_rendered = True
        confirmation(**kwargs)

    #: 2026-10-31（用户反馈：卡片该消失不消失、乱出现、顺序乱）：业务卡改由 **panel_router 统一裁决** —
    #: 一屏最多一张；解析卡待选 / 在途确认时业务卡一律让位；换面板时复位上一张的展开态。
    import panel_router
    from agent import confirm_card as _confirm_card, payee_picker as _payee_picker

    last_turn = (st.session_state.get('turns') or [{}])[-1].get('turn') or {}
    has_confirmation = bool(executor and executor.submit(_confirm_card.current, session).result())
    picker_pending = bool(executor and executor.submit(_payee_picker.pending, session).result())
    panel = panel_router.active_panel(intent=last_turn.get('intent'),
                                      has_confirmation=has_confirmation,
                                      picker_pending=picker_pending,
                                      flags=st.session_state)
    previous = st.session_state.get('workspace-active-panel')
    if previous != panel:                       # 换卡 → 复位离场那张卡的展开/编辑态
        for key, value in panel_router.clears_state(previous, panel).items():
            st.session_state[key] = value
        st.session_state['workspace-active-panel'] = panel
    renderers = {'payee': payee_card_ui.render, 'schedule': schedule_card_ui.render,
                 'aa': aa_card_ui.render, 'gift': gift_card_ui.render}
    if panel in renderers:
        if panel == 'payee':
            renderers[panel](executor, session, confirmation=inline_confirmation)
        else:
            renderers[panel](executor, session)
    if scene not in ('cards', 'records'):
        scene_ui.render(executor, session, ask, show_scope=False)
    if not (scene == 'payee_list' and st.session_state.get('payee-inline-intent')):
        slot_form_ui.render(executor, session)
    #: 2026-10-31 修：只有**群收款本身**的确认卡由群收款卡自己画（三卡合一）；
    #: 「登记已付」等其他在途确认仍走通用确认卡 —— 否则登记已付没有确认按钮，流程卡死。
    _pending_conf = (executor.submit(_confirm_card.current, session).result()
                     if executor else None)
    _aa_owns = getattr(_pending_conf, 'intent', '') == 'aa_collect'
    if not inline_confirmation_rendered and not _aa_owns:
        confirmation()
