"""银行卡卡面和轻量动效；金额和卡号只由调用方提供。"""
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v2 as components

_FOCUS_JS = '\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8')
                     for name in ('card_chains.js', 'card_focus.js'))


CSS = """<style>
.bank-gallery-state,.bank-card-identity {display:none}
.bank-chain-overlay {position:absolute;inset:0;width:100%;height:100%;z-index:3;
 pointer-events:none;filter:drop-shadow(0 3px 2px #07132299);border-radius:20px}
.bank-chain-badge {transform-box:fill-box;transform-origin:center}
.st-key-bank-gallery {position:relative;isolation:isolate}
.st-key-bank-gallery [class*="st-key-bank-tile-"] {margin-bottom:0}
.st-key-bank-gallery-details:has(.st-key-detail-close) {animation:bank-details-reveal .5s ease .4s both}
.st-key-bank-gallery:not([data-focus=""]) [class*="st-key-detail-"] button:hover {animation:none;transform:none}
@keyframes bank-details-reveal {
 from {opacity:0;transform:translateY(20px);filter:blur(4px)}
 to {opacity:1;transform:translateY(0);filter:blur(0)}
}
/* Only the gallery face is animated; action buttons and other pages stay still. */
[class*="st-key-bank-tile-"] {margin-bottom:1.1rem}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button {
 position:relative;display:flex;align-items:flex-end;justify-content:flex-start;
 width:100%;min-height:208px;padding:28px;border:1px solid #ffffff33;
 border-radius:20px;background:radial-gradient(ellipse at 100% 0%,#3f7189 0%,transparent 58%),
 linear-gradient(125deg,#102a43,#183c51);color:#fff;overflow:hidden;
 box-shadow:0 12px 26px -14px #102a4377;isolation:isolate;
 transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease;
}
[class*="st-key-bank-tile-credit-"] [class*="st-key-detail-"] button {
 background:radial-gradient(ellipse at 100% 0%,#726b9c 0%,transparent 60%),linear-gradient(125deg,#292541,#4c4169);
}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button p {
 color:#fff;font-family:ui-monospace,Consolas,monospace;font-size:clamp(17px,1.7vw,23px);
 letter-spacing:.12em;margin:0 0 17px;text-align:left;font-weight:600;
}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button::before {
 content:"模拟银行  /  储蓄卡";position:absolute;left:28px;top:24px;
 color:#e5edf5;font-size:14px;letter-spacing:.08em;
}
[class*="st-key-bank-tile-credit-"] [class*="st-key-detail-"] button::before {content:"模拟银行  /  信用卡"}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button::after {
 content:"";position:absolute;left:28px;top:68px;width:40px;height:30px;
 border-radius:7px;border:1px solid #d1b777;
 background:linear-gradient(90deg,transparent 46%,#b49b5e 48%,transparent 51%),
 linear-gradient(0deg,#d5bd81 30%,#b49b5e 32%,#f0deaa 34%,#ddc78e 65%,#b49b5e 67%,#e8d59f 69%);
}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button:hover {
 color:white;border-color:#ffffff88;transform:translateY(-4px);
 box-shadow:0 18px 30px -14px #102a4399;animation:bank-card-sway .72s ease-in-out 1;
}
[class*="st-key-bank-tile-"] [class*="st-key-detail-"] button:focus-visible {
 outline:3px solid #e58b48;outline-offset:4px;
}
[class*="st-key-bank-selected-"] [class*="st-key-detail-"] button {
 outline:2px solid #e5a25d;outline-offset:3px;
}
@keyframes bank-card-sway {
 0%,100%{transform:translateY(-4px) rotate(0deg)}
 25%{transform:translateY(-5px) rotate(-1deg)}
 65%{transform:translateY(-5px) rotate(.75deg)}
}
@media(prefers-reduced-motion:reduce) {
 .st-key-bank-gallery-details:has(.st-key-detail-close) {animation:none}
 [class*="st-key-bank-tile-"] [class*="st-key-detail-"] button,
 [class*="st-key-bank-tile-"] [class*="st-key-detail-"] button:hover {
 animation:none;transition:none;transform:none;
 }
}
</style>"""


def styles() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def face(item: dict) -> bool:
    return st.button(item['card_no_mask'], key=f"detail-{item['card_id']}",
                     help='点击卡片查看详情', width='stretch')


def deck(items: list[dict], selected: str | None, render_card, revision: int = 0) -> None:
    styles()
    with st.container(key='bank-gallery'):
        st.markdown(f'<span class="bank-gallery-state" data-selected="{escape(selected or "", quote=True)}" data-revision="{revision}"></span>', unsafe_allow_html=True)
        for item in items:
            with st.container(key=f"bank-tile-{item['type']}-{item['card_id']}"):
                st.markdown(f'<span class="bank-card-identity" data-card="{escape(item["card_id"], quote=True)}" data-status="{escape(item["status"], quote=True)}"></span>', unsafe_allow_html=True)
                render_card(item)
    animator = components.component('bank_card_focus', js=_FOCUS_JS)
    animator(key='cards-focus-animation')
