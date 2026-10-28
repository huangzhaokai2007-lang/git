"""Replace bill category tables with one facts-backed, browser-animated pie."""
import hashlib
from collections.abc import Callable
from pathlib import Path
import re

import streamlit as st
import streamlit.components.v2 as components

from interfaces.web import reply_animation

_TABLE = re.compile(r'^\|\s*(?:分类|项目|支出分类)\s*\|\s*金额（元）\s*\|\s*占比\s*\|[^\n]*\n'
                    r'(?:\|[^\n]*(?:\n|$))+', re.MULTILINE)
_CSS = Path(__file__).with_suffix('.css').read_text(encoding='utf-8')
_JS = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')


def _selection(widget: str, turn: dict, loader: Callable | None) -> tuple[str | None, dict | None]:
    selection = st.session_state.get(widget, {}).get('selected')
    selected = selection.get('category') if isinstance(selection, dict) else selection
    if selected not in [group.get('key') for group in turn['facts']['groups']]:
        st.session_state.pop(widget + '-detail', None)
        return None, None
    cache_key = widget + '-detail'
    cached = st.session_state.get(cache_key) or {}
    if loader is not None and cached.get('category') != selected:
        try:
            detail = loader(turn, selected)
        except Exception:
            detail = {'ok': False, 'message': '暂时无法读取明细，请收起后重新选择。'}
        cached = {'category': selected, 'detail': detail}
        st.session_state[cache_key] = cached
    return selected, cached.get('detail')


def render_reply(text: str, turn: dict, *, key: str, animate: bool,
                 detail_loader: Callable | None = None) -> bool:
    """Only replace the category table; original messages/facts remain untouched."""
    facts = turn.get('facts') or {}
    groups = facts.get('groups') or []
    if (turn.get('intent') not in ('bill_analysis', 'bill_report') or turn.get('error_code')
            or not groups or not (table := _TABLE.search(text))):
        return False
    before, after = text[:table.start()].strip(), text[table.end():].strip()
    if before:
        reply_animation.render(before, key + '-before', animate=animate)
    identity = hashlib.sha256(key.encode()).hexdigest()[:24]
    widget = f'pie-{key}'
    selected, detail = _selection(widget, turn, detail_loader)
    selection = st.session_state.get(widget, {}).get('selected')
    focus = selection if isinstance(selection, dict) else {}
    pie = components.component('spending_pie',
        html='<section class="bank-pie" aria-label="支出分类占比"></section>',
        css=_CSS, js=_JS, isolate_styles=False)
    pie(key=widget, default={'selected': None}, on_selected_change=lambda: None,
        data={'groups': groups, 'animation_id': identity, 'period': facts.get('period'),
              'selected': selected, 'detail': detail,
              'focus_target': focus.get('focus_target'), 'focus_id': focus.get('focus_id'),
              'total': facts.get('total_yuan') or facts.get('out_sum_yuan')})
    if after:
        st.markdown(after)
    return True
