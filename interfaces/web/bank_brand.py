"""银行「品牌感」素材：**纯 CSS 渐变 + 内联 SVG**（不引依赖、不联网、不用真实商标）。

为什么不用图片：比赛评测环境要求一条命令就能跑起来、且**禁止新依赖**，
所以把 logo 画成内联 SVG 几何标记（每家一种形状 + 缩写字），配色取各行大致的品牌色，
既好看又不侵犯商标、还能离线渲染。

对外只有四个函数：
- `style_css()`：一次性注入的样式（卡片 + 「选这张」按钮的外观）；
- `card_html(card, label)`：一张银行卡的视觉块（渐变底 + logo + 卡号掩码 + 卡种）；
- `button_css(card_id)`：给按钮加配套样式的 key（供 `st.button(key=...)` 用）；
- `slug(bank)`：类名用的稳定 slug。
"""

from __future__ import annotations

import html

#: 银行 → (渐变起色, 渐变止色, 高光色)。**深色卡**配色：低饱和、压明度，靠金线提气。
_GRADIENTS: dict[str, tuple[str, str, str]] = {
    "建设银行": ("#0A1C33", "#123857", "#2E6C9E"),
    "农业银行": ("#0B1F17", "#164434", "#2C7A5A"),
    "中国银行": ("#2A0E10", "#4A1A1C", "#8C4340"),
    "工商银行": ("#2B0A0E", "#4C1519", "#8E3A34"),
    "招商银行": ("#26090A", "#451213", "#8A3A32"),
    "交通银行": ("#0A1730", "#152A50", "#2F5C93"),
    "邮储银行": ("#0A1E15", "#12402C", "#2A6E4C"),
    "中信银行": ("#250A0D", "#431518", "#7E3A36"),
    "浦发银行": ("#08182B", "#0F2E4C", "#2A5C87"),
    "民生银行": ("#0A1C18", "#123B31", "#2A6B58"),
    "兴业银行": ("#08182C", "#123050", "#2C5A8A"),
    "光大银行": ("#210A16", "#3B1626", "#7A3550"),
    "华夏银行": ("#26090F", "#43161C", "#7C363B"),
    "广发银行": ("#280A0C", "#481518", "#824039"),
    "平安银行": ("#2A0A0B", "#4A1414", "#84403A"),
}
_FALLBACK = ("#15161A", "#23252C", "#4A4E5A")

#: 每家一个几何标记（viewBox 0 0 48 48，白色描边；纯几何，不是任何真实行标）
_GLYPHS: dict[str, str] = {
    "建设银行": '<path d="M10 30v-9h28v9"/><path d="M14 21V13h20v8"/>',
    "农业银行": '<path d="M24 10v28"/><path d="M12 20c6-6 18-6 24 0"/><path d="M12 32c6-6 18-6 24 0"/>',
    "中国银行": '<rect x="12" y="12" width="24" height="24" rx="4"/><rect x="21" y="21" width="6" height="6"/>',
    "工商银行": '<circle cx="24" cy="24" r="13"/><circle cx="24" cy="24" r="4"/>',
    "招商银行": '<path d="M12 34L24 12l12 22z"/>',
    "交通银行": '<rect x="12" y="14" width="24" height="8" rx="4"/><rect x="12" y="26" width="24" height="8" rx="4"/>',
    "邮储银行": '<path d="M24 11l12 9v12l-12 9-12-9V20z"/>',
    "中信银行": '<path d="M24 11v26"/><path d="M12 24h24"/>',
    "浦发银行": '<path d="M12 30c8-14 16-14 24 0"/><circle cx="24" cy="16" r="4"/>',
    "民生银行": '<circle cx="18" cy="24" r="7"/><circle cx="30" cy="24" r="7"/>',
    "兴业银行": '<path d="M12 24l12-12 12 12-12 12z"/>',
    "光大银行": '<circle cx="24" cy="24" r="13"/><path d="M24 11v26"/>',
    "华夏银行": '<path d="M14 34V14l20 20V14"/>',
    "广发银行": '<path d="M12 26h24"/><path d="M24 12v8"/><path d="M18 34h12"/>',
    "平安银行": '<path d="M14 34V20a10 10 0 0120 0v14"/>',
}
_FALLBACK_GLYPH = '<rect x="13" y="18" width="22" height="15" rx="3"/><path d="M13 24h22"/>'

#: 卡种中文
_TYPE_CN = {"savings": "储蓄卡", "credit": "信用卡"}


def slug(bank: str) -> str:
    """类名用 slug：中文会被 CSS 直接支持（用属性选择器），这里给一个稳定英文键。"""
    return f"bk{abs(hash(bank)) % 100000}"


def _colors(bank: str) -> tuple[str, str, str]:
    return _GRADIENTS.get(str(bank), _FALLBACK)


def _glyph(bank: str) -> str:
    return _GLYPHS.get(str(bank), _FALLBACK_GLYPH)


def type_cn(card: dict) -> str:
    return _TYPE_CN.get(str(card.get("card_type") or ""), str(card.get("card_type") or "银行卡"))


def card_html(card: dict, *, holder: str = "") -> str:
    """一张银行卡的视觉块（渐变 + logo + 掩码 + 卡种 + 持有人）。"""
    bank = str(card.get("bank") or "本行")
    start, end, glow = _colors(bank)
    mask = html.escape(str(card.get("card_no_mask") or "****"))
    bank_esc = html.escape(bank)
    holder_esc = html.escape(holder)
    kind = type_cn(card)
    return (
        f'<div class="bkcard" style="--bk-a:{start};--bk-b:{end};--bk-glow:{glow};">'
        f'  <div class="bkcard-logo">'
        f'    <svg viewBox="0 0 48 48" fill="none" stroke="#fff" stroke-width="2.6" '
        f'         stroke-linecap="round" stroke-linejoin="round">{_glyph(bank)}</svg>'
        f'  </div>'
        f'  <div class="bkcard-body">'
        f'    <div class="bkcard-bank">{bank_esc}<span class="bkcard-kind">{kind}</span></div>'
        f'    <div class="bkcard-no">{mask}</div>'
        f'    <div class="bkcard-holder">{holder_esc}</div>'
        f'  </div>'
        f'  <div class="bkcard-chip"></div>'
        f'  <svg class="bkcard-wave" viewBox="0 0 24 24" fill="none" stroke="#fff" '
        f'       stroke-width="1.8" stroke-linecap="round">'
        f'    <path d="M6 8a8 8 0 0112 0"/><path d="M9 12a4.2 4.2 0 016 0"/>'
        f'    <circle cx="12" cy="16.4" r="1"/></svg>'
        f'</div>'
    )


def button_css(key: str) -> str:
    """给「选这张卡」的按钮挂一个类名（Streamlit 会给带 key 的控件加 `st-key-<key>`）。"""
    return f"st-key-{key}"


def style_css() -> str:
    """一次注入：卡片外观 + 「选这张卡」按钮贴合卡片底部。"""
    return """
<style>
.bkcard{
  position:relative;display:flex;gap:14px;align-items:center;
  padding:20px 22px 18px;border-radius:18px 18px 6px 6px;color:#F3F1EA;
  background:
    radial-gradient(120% 150% at 10% -10%,var(--bk-glow) 0%,transparent 42%),
    linear-gradient(150deg,var(--bk-a) 0%,var(--bk-b) 68%,var(--bk-a) 100%);
  border:1px solid rgba(198,166,102,.28);
  box-shadow:0 18px 40px -22px rgba(0,0,0,.95), 0 1px 0 rgba(255,255,255,.05),
             inset 0 0 0 1px rgba(0,0,0,.35);
  overflow:hidden;
}
.bkcard::before{                /* 右上暗角，增加纵深 */
  content:"";position:absolute;inset:0;
  background:radial-gradient(90% 80% at 100% 0%,rgba(0,0,0,.55),transparent 60%);
  pointer-events:none;
}
.bkcard::after{                 /* 极弱高光 */
  content:"";position:absolute;inset:0 0 auto 0;height:38%;
  background:linear-gradient(180deg,rgba(255,255,255,.07),transparent);
  pointer-events:none;
}
.bkcard-logo{
  flex:0 0 46px;width:46px;height:46px;border-radius:14px;
  display:flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.06);border:1px solid rgba(198,166,102,.26);
  backdrop-filter:blur(2px);
}
.bkcard-logo svg{width:28px;height:28px}
.bkcard-body{flex:1 1 auto;min-width:0;position:relative;z-index:1}
.bkcard-chip{
  position:absolute;right:20px;bottom:16px;width:28px;height:20px;border-radius:4px;
  background:linear-gradient(135deg,#f6e7b4,#c8a44a 60%,#f3e2ac);
  box-shadow:inset 0 0 0 1px rgba(0,0,0,.22);opacity:.92;
}
.bkcard-wave{position:absolute;right:16px;top:16px;width:22px;height:22px;opacity:.5}
.bkcard-bank{font-size:15px;font-weight:600;letter-spacing:.06em;display:flex;
  align-items:center;gap:8px;color:#fff}
.bkcard-kind{
  font-size:10px;font-weight:600;padding:1px 8px;border-radius:999px;
  color:#D8C48F;background:rgba(198,166,102,.12);border:1px solid rgba(198,166,102,.32);
  letter-spacing:.08em;
}
.bkcard-no{margin-top:6px;font-size:17px;letter-spacing:.09em;font-variant-numeric:tabular-nums;
  color:#EAE6DA}
.bkcard-holder{margin-top:5px;font-size:11px;letter-spacing:.14em;
  color:rgba(232,226,208,.55);min-height:14px}
div[class*="st-key-pickcard-"] button{
  margin-top:-2px;border-radius:6px 6px 16px 16px;border:1px solid rgba(198,166,102,.22);
  background:linear-gradient(180deg,rgba(28,26,24,.92),rgba(16,16,18,.96));
  color:#D8C48F;font-weight:600;font-size:12px;letter-spacing:.16em;
  padding:10px 0;
}
div[class*="st-key-pickcard-"] button:hover{
  background:linear-gradient(180deg,rgba(44,40,32,.98),rgba(22,22,24,.98));
  color:#F1DFAE;border-color:rgba(216,196,143,.45);
}
</style>
"""
