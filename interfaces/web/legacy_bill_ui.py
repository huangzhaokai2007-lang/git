"""Original main bill/ledger presentation, retained independently of other scenes."""
from datetime import date, timedelta

import streamlit as st
from agent import bill_detail_flow
from interfaces.web import components
from interfaces.web import subscription_ui
import interfaces.web.insight_card_ui as insight_card_ui

#: 账单报告「先选时间段再看正文」的会话标记（选过之后才展示报告详情）。
RANGE_KEY = 'bill-range'


def open_picker() -> None:
    st.session_state.pop(RANGE_KEY, None)
    st.session_state['workspace-result'] = {'scene': 'bills', 'prompt': '看账单',
        'turn': {'intent': 'bill_report'}}
    st.session_state['messages'].append({'role': 'user', 'text': '看账单'})


def _latest_intent(messages) -> str:
    for message in reversed(messages):
        intent = (message.get('turn') or {}).get('intent')
        if intent:
            return str(intent)
    return ''


def _range_picker(ask) -> None:
    """账单报告：先给一个时间段选项，选完才出报告（用户 2026-10-29 的要求）。

    - 两个日期各是一个**点开才展开**的小框（`st.date_input`），选项不铺在页面上；
    - 起止都限制在**今天及以前**：账单是历史，选到未来没有数据；
    - 选完点「生成账单报告」才交给编排层（界面不算账、不取数）。
    """
    today = date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    default_start = last_month_end.replace(day=1)          # 默认 = 上个月整月（有数据的那一期）
    st.markdown('##### 选择账单时间段')
    st.caption('点开小框挑选起止日期，选好后生成报告。可选范围：最早到 2015 年 1 月，最晚到今天。')
    left, right = st.columns(2)
    start = left.date_input('开始日期', value=default_start, max_value=today,
                            min_value=date(2015, 1, 1), format='YYYY-MM-DD',
                            key='bill-range-start')
    end = right.date_input('结束日期', value=last_month_end, max_value=today,
                           min_value=date(2015, 1, 1), format='YYYY-MM-DD',
                           key='bill-range-end')
    st.caption(f'时间段：{start.year}年{start.month}月{start.day}日 —— '
               f'{end.year}年{end.month}月{end.day}日')
    if st.button('生成账单报告', type='primary', key='bill-range-go', disabled=start > end):
        if start > end:
            st.error('开始日期不能晚于结束日期。')
            return
        st.session_state[RANGE_KEY] = (start.isoformat(), end.isoformat())
        said = (f'{start.year}年{start.month}月{start.day}日至'
                f'{end.year}年{end.month}月{end.day}日的账单报告')
        ask(said, rerun=False)
        st.rerun()


def _without_reports(messages) -> list[dict]:
    """还没选时间段时，报告正文先不展示（其余对话照旧）。"""
    return [message for message in messages
            if (message.get('turn') or {}).get('intent') != 'bill_report']


def _latest_report(messages) -> dict | None:
    """最近一条**成功**的账单报告回执（订阅块挂在它下面）。"""
    for message in reversed(messages):
        turn = message.get('turn') or {}
        if turn.get('intent') == 'bill_report' and not turn.get('error_code'):
            return turn
    return None


def _subscriptions(turn, ask) -> None:
    """Comment 7：报告里的「订阅（进行中）」= 「快捷场景 → 管订阅」那一块。

    同组件（`subscription_ui`）、同一份数据（工具层 `list_subscriptions("active")` 的 items，
    随账单报告 facts 一起下来），报告 Markdown 里那段文字 bullet 已经删掉了。
    """
    items = list((turn.get('facts') or {}).get('subscriptions') or [])
    if not items:
        return
    subscription_ui.render_turn(
        {'intent': 'subscription_list', 'facts': {'status': 'active', 'items': items}},
        ask=ask, key_prefix='bill-subs-' + str(turn.get('trace_id') or ''),
        heading='订阅（进行中）')


def _last_user_index(messages) -> int:
    """最后一条**用户发言**的下标（时间段选择要贴在它下面）。"""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get('role') == 'user':
            return index
    return len(messages) - 1


def _transcript(messages, executor, session: str, ask, *, readonly: bool) -> None:
    components.transcript(messages, compact=True, legacy_bills=True,
        insight_card=lambda turn, key: insight_card_ui.render(turn,
            key='origin-' + key if readonly else key, on_ask=ask, readonly=readonly),
        bill_details=None if readonly else lambda source, category: executor.submit(
            bill_detail_flow.load, session, source, category).result())


def render(messages, executor, session: str, ask, *, readonly: bool = False) -> None:
    #: Comment 14：说完「看账单」后**不要**先甩出上个月的报告 —— 时间段选择直接贴在那句
    #: 话下面，选完点「生成账单报告」才出对应期间的正文（其余内容这一轮都不画）。
    waiting = (not readonly and (st.session_state.get('workspace-result') or {}).get('turn', {}).get('intent') == 'bill_report'
               and st.session_state.get(RANGE_KEY) is None)
    if waiting:
        _transcript(_without_reports(messages[:_last_user_index(messages) + 1]), executor, session, ask,
                    readonly=readonly)
        _range_picker(ask)
        st.caption('选好时间段后，这里显示对应期间的账单报告。')
        return
    latest_report = next((i for i in range(len(messages) - 1, -1, -1)
                          if (messages[i].get('turn') or {}).get('intent') == 'bill_report'), -1)
    displayed = [m for i, m in enumerate(messages)
                 if (m.get('turn') or {}).get('intent') != 'bill_report' or i == latest_report]
    _transcript(displayed, executor, session, ask, readonly=readonly)
    if not readonly and (report := _latest_report(messages)):
        _subscriptions(report, ask)
    if not readonly:
        _range_picker(ask)          # 已选过也留着：随时可以换个时间段重出报告
