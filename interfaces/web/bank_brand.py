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

#: 银行 → (渐变起色, 渐变止色, 高光色)。配色是**示意性**的，不追求与真行一致。
_GRADIENTS: dict[str, tuple[str, str, str]] = {
    "建设银行": ("#0B4EA2", "#1D74D0", "#6FB3F2"),
    "农业银行": ("#0E7A3C", "#35B06A", "#8BE0AE"),
    "中国银行": ("#8B1A1A", "#C6353A", "#F08A8A"),
    "工商银行": ("#B4101F", "#E24A3F", "#FFA08F"),
    "招商银行": ("#A80007", "#E23A32", "#FF9A8F"),
    "交通银行": ("#0A2E6E", "#1657B8", "#7FA9EC"),
    "邮储银行": ("#00693E", "#14A05A", "#7FD6A6"),
    "中信银行": ("#8E1B22", "#D0403A", "#F49A93"),
    "浦发银行": ("#00447C", "#0B69B0", "#7FB6E6"),
    "民生银行": ("#00543F", "#0F8A6A", "#77D8B6"),
    "兴业银行": ("#003F88", "#1F74C8", "#86B7EC"),
    "光大银行": ("#7A1240", "#C74B7F", "#F09ABE"),
    "华夏银行": ("#8E1626", "#D5454F", "#F49A9E"),
    "广发银行": ("#A8101C", "#EA5A4B", "#FFA894"),
    "平安银行": ("#B0000E", "#F0524A", "#FFA294"),
}
_FALLBACK = ("#2B2D35", "#4A4C58", "#9AA0AE")

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
  padding:18px 20px 16px;border-radius:18px 18px 6px 6px;color:#fff;
  background:radial-gradient(120% 160% at 8% 0%,var(--bk-glow) 0%,transparent 46%),
             linear-gradient(135deg,var(--bk-a) 0%,var(--bk-b) 100%);
  box-shadow:0 12px 28px -14px rgba(0,0,0,.75), inset 0 1px 0 rgba(255,255,255,.22);
  overflow:hidden;
}
.bkcard::after{                 /* 顶部高光 */
  content:"";position:absolute;inset:0 0 auto 0;height:52%;
  background:linear-gradient(180deg,rgba(255,255,255,.16),transparent);
  pointer-events:none;
}
.bkcard-logo{
  flex:0 0 46px;width:46px;height:46px;border-radius:14px;
  display:flex;align-items:center;justify-content:center;
  background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);
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
.bkcard-bank{font-size:15px;font-weight:700;letter-spacing:.02em;display:flex;
  align-items:center;gap:8px;color:#fff}
.bkcard-kind{
  font-size:11px;font-weight:600;padding:1px 7px;border-radius:999px;
  background:rgba(255,255,255,.22);border:1px solid rgba(255,255,255,.3);
}
.bkcard-no{margin-top:6px;font-size:17px;letter-spacing:.09em;font-variant-numeric:tabular-nums;
  color:rgba(255,255,255,.96)}
.bkcard-holder{margin-top:4px;font-size:12px;color:rgba(255,255,255,.78);min-height:15px}
div[class*="st-key-pickcard-"] button{
  margin-top:-6px;border-radius:6px 6px 16px 16px;border:0;
  background:rgba(255,255,255,.10);color:#fff;font-weight:600;font-size:13px;
  padding:9px 0;
}
div[class*="st-key-pickcard-"] button:hover{
  background:rgba(255,255,255,.20);color:#fff;border:0;
}
</style>
"""
