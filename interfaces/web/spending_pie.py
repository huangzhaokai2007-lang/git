"""Replace bill category tables with one facts-backed, browser-animated pie."""
import hashlib
from pathlib import Path
import re

import streamlit as st
import streamlit.components.v2 as components

from interfaces.web import reply_animation

_TABLE = re.compile(r'^\|\s*(?:分类|项目|支出分类)\s*\|\s*金额（元）\s*\|\s*占比\s*\|[^\n]*\n'
                    r'(?:\|[^\n]*(?:\n|$))+', re.MULTILINE)
_CSS = Path(__file__).with_suffix('.css').read_text(encoding='utf-8')
_JS = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')


def render_reply(text: str, turn: dict, *, key: str, animate: bool) -> bool:
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
    pie = components.component('spending_pie',
        html='<section class="bank-pie" aria-label="支出分类占比"></section>',
        css=_CSS, js=_JS, isolate_styles=False)
    pie(key=f'pie-{key}', data={'groups': groups, 'animation_id': identity,
                              'total': facts.get('total_yuan') or facts.get('out_sum_yuan')})
    if after:
        st.markdown(after)
    return True
