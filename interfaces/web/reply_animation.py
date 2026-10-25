"""Presentation-only progressive replies; original Markdown remains intact."""
import math
import time

import streamlit as st


def chunks(text: str):
    # Keep both total duration and update count bounded for long reports.
    step = max(1, math.ceil(len(text) / 500))
    count = max(1, math.ceil(len(text) / step))
    delay = min(.024, 2.6 / count)
    for offset in range(0, len(text), step):
        yield text[offset:offset + step]
        time.sleep(delay)


def render(text: str, key: str, *, animate: bool) -> None:
    seen = st.session_state.setdefault('reply-animation-seen', set())
    fresh = key not in seen
    seen.add(key)
    if not animate or not fresh or st.get_option('global.appTest'):
        st.markdown(text)
        return
    st.markdown('<span class="reply-reveal-marker" aria-hidden="true"></span>',
                unsafe_allow_html=True)
    st.write_stream(chunks(text))
