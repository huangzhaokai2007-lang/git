"""「高级感」动效层（纯 CSS，零 JS、零依赖）。

设计原则（金融产品而不是玩具）：
- **短**：150–220ms；**轻**：位移 ≤ 8px、缩放 ≤ 2%；
- **有意图**：入场表示"新内容来了"，脉冲表示"这个数字变了"，hover 表示"可点"；
- **可关**：`prefers-reduced-motion` 下全部关闭（无障碍，也是"专业"的一部分）。

注入方式：`premium_ui.inject_css()` 一次性插入 `<style>`（Streamlit 每次 rerun 重渲染，重复无害）。
"""

from __future__ import annotations

#: 卡片入场 + 悬停 + 语义色条
CARD_CSS = """
/* 只动 transform、不动 opacity：Streamlit 重渲染替换 DOM 时，opacity 动画会「闪一下」。 */
@keyframes dsh-rise { from { transform: translateY(6px) scale(.997); }
                      to   { transform: none; } }
@keyframes dsh-glow { 0%,100% { box-shadow: 0 0 0 0 rgba(102,103,201,0); }
                      50%     { box-shadow: 0 0 0 3px rgba(102,103,201,.18); } }
/* 任何带边框的容器（我们的"卡片"）入场 */
div[data-testid="stVerticalBlockBorderWrapper"] {
  animation: dsh-rise .14s ease-out both;
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0,0,0,.28);
}
/* 同一屏多张卡：按顺序轻微错峰，观感更"有生命" */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(2) { animation-delay: .04s; }
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(3) { animation-delay: .08s; }
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(4) { animation-delay: .12s; }
/* 管线条：每一步依次淡入（"AI 在思考"的观感） */
@keyframes dsh-step { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
.dsh-pipe { font-size: .82rem; color: #9aa0ac; margin: .1rem 0 .4rem 0; letter-spacing: .2px; }
.dsh-pipe b { color: #d1d4dc; font-weight: 600; }
.dsh-step { display: inline-block; animation: dsh-step .22s ease-out both; }
/* 语义色条（卡头） */
.dsh-head { border-left: 3px solid #6667C9; padding: .1rem 0 .1rem .6rem; margin-bottom: .35rem;
            transition: border-color .16s ease; }
.dsh-head .t { font-size: 1.02rem; font-weight: 650; color: #E6E8EE; }
.dsh-head .s { font-size: .78rem; color: #9aa0ac; margin-top: .1rem; }
.dsh-head:hover { border-left-color: #9790E5; }
.dsh-tone-ok { border-left-color: #22C55E; }
.dsh-tone-warn { border-left-color: #F59E0B; }
.dsh-tone-danger { border-left-color: #EF4444; }
.dsh-tone-draft { border-left-color: #6B7280; }
"""

#: 表格：行入场错峰 / hover 高亮 / 表头吸顶 / 数字等宽右对齐
TABLE_CSS = """
div[data-testid="stMarkdownContainer"] table,
div[data-testid="stChatMessage"] table,
div[data-testid="stChatMessage"] div[data-testid="stMarkdownContainer"] table {
  border-collapse: separate; border-spacing: 0; width: 100%;
  font-variant-numeric: tabular-nums; font-size: .86rem;
  border: 1px solid #2C2D33; border-radius: 10px; overflow: hidden;
}
div[data-testid="stMarkdownContainer"] thead th {
  position: sticky; top: 0; z-index: 1;
  background: #1C1D21; color: #C9CDD6; font-weight: 600;
  border-bottom: 1px solid #2C2D33; padding: .5rem .6rem; text-align: left;
}
div[data-testid="stMarkdownContainer"] tbody td {
  padding: .45rem .6rem; border-bottom: 1px solid rgba(44,45,51,.6);
  transition: background-color .12s ease;
}
/* 注意：回执是**打字机式**逐字渲染的，行级淡入会随每次刷新反复触发 → 看起来在抖，故不做。 */
div[data-testid="stMarkdownContainer"] tbody tr:nth-child(even) { background: rgba(255,255,255,.015); }
div[data-testid="stMarkdownContainer"] tbody tr:hover { background: rgba(102,103,201,.10); }
div[data-testid="stMarkdownContainer"] tbody tr:last-child td { border-bottom: none; }
"""

#: 数字：等宽 + "刚变过"的脉冲（给需要强调的数值加 class="dsh-num dsh-flash"）
NUM_CSS = """
@keyframes dsh-flash { 0% { background-size: 0% 100%; } 100% { background-size: 100% 100%; } }
.dsh-num, [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
  font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;
}
[data-testid="stMetricValue"] { animation: dsh-rise .18s ease-out both; }
.dsh-flash {
  background-image: linear-gradient(90deg, rgba(102,103,201,.28), rgba(102,103,201,0));
  background-repeat: no-repeat; background-size: 0% 100%;
  animation: dsh-flash .9s ease-out .1s 1 forwards;
  border-radius: 4px; padding: 0 .15rem;
}
"""

#: 控件：按钮 / 胶囊 / 进度条 / 折叠面板 / 提示条
WIDGET_CSS = """
/* 按钮：hover 抬起、按下回弹 */
.stButton > button { transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease; }
.stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,.22); }
.stButton > button:active { transform: translateY(1px) scale(.995); }
/* 示例胶囊 / 选择胶囊 */
[data-testid="stPills"] button, .stPills button {
  transition: transform .12s ease, background-color .12s ease;
}
[data-testid="stPills"] button:hover, .stPills button:hover { transform: translateY(-1px); }
/* 进度条（额度使用率）：宽度平滑增长 + 顶部微光 */
@keyframes dsh-bar { from { width: 0%; } }
@keyframes dsh-sheen { 0% { opacity: .15; } 50% { opacity: .45; } 100% { opacity: .15; } }
[data-testid="stProgress"] > div > div > div { animation: dsh-bar .55s ease-out both; }
[data-testid="stProgress"] > div > div > div > div {
  animation: dsh-sheen 2.4s ease-in-out infinite; border-radius: 999px;
}
/* 折叠面板展开：内容淡入 */
[data-testid="stExpander"] details[open] > div { animation: dsh-rise .16s ease-out both; }
[data-testid="stExpander"] summary { transition: color .12s ease; }
[data-testid="stExpander"] summary:hover { color: #9790E5; }
/* 提示条 / 成功 / 错误：轻微滑入 */
@keyframes dsh-slide { from { opacity: 0; transform: translateX(-6px); } to { opacity: 1; transform: none; } }
[data-testid="stAlert"] { animation: dsh-slide .18s ease-out both; }
/* toast（Streamlit 的浮层） */
[data-testid="stToast"] { animation: dsh-rise .2s ease-out both; }
"""

#: 无障碍：用户系统设置"减少动效"时全部关闭
REDUCED_MOTION_CSS = """
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
"""


#: 收款人列表（图一风格）+ 展开详情（图二风格）
PAYEE_CSS = """
@keyframes dsh-expand { from { opacity: 0; transform: translateY(-4px) scaleY(.985); }
                        to   { opacity: 1; transform: none; } }
.dsh-payee-row { display: flex; align-items: center; gap: 14px; padding: 8px 4px; }
.dsh-payee-avatar { width: 30px; height: 30px; flex: 0 0 30px; border-radius: 50%;
  background: #2b2940; color: #b7b1ff; display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 650; }
.dsh-payee-name { min-width: 64px; font-weight: 650; font-size: 15px; color: #E6E8EE; }
.dsh-payee-phone { font-family: ui-monospace, Consolas, monospace; letter-spacing: .3px;
  color: #d1d4dc; font-size: 14px; }
.dsh-payee-bank { color: #9aa0ac; font-size: 13.5px; }
/* 行 hover：整行轻染（配合 ▸ 有可点暗示） */
div[class*="st-key-payee-row-"]:hover { background: rgba(102,103,201,.10); border-radius: 10px; }
/* 展开的详情：拉开动画 */
.st-key-payee-verification { animation: dsh-slide .18s ease-out both; }
.st-key-payee-verification [data-testid="stVerticalBlock"] { gap: .55rem; }
.st-key-payee-detail { animation: dsh-expand .18s ease-out both; }
/* 「编辑资料」在名字下方、字大一点 */
.st-key-payee-detail [data-testid="stExpander"] summary p { font-size: 1.02rem; font-weight: 600; }
"""


#: 收款人「整行按钮」——把 Streamlit 的方块按钮改造成列表行（悬停才浮现提示）
PAYEE_ROW_CSS = """
div[class*="st-key-payee-row-"] button {
  background: transparent !important; border: none !important; box-shadow: none !important;
  justify-content: flex-start !important; text-align: left !important;
  padding: .3rem 0 !important; min-height: 2.2rem !important; border-radius: 10px !important;
  font-weight: 400 !important; color: #9298a3 !important;
  transition: background .12s ease, transform .12s ease;
}
div[class*="st-key-payee-row-"] button > div,
div[class*="st-key-payee-row-"] button > div > span {
  width: 100%; justify-content: flex-start !important;
}
div[class*="st-key-payee-row-"] button [data-testid="stMarkdownContainer"] {
  width: 100%; text-align: left !important;
}
div[class*="st-key-payee-row-"] button p { text-align: left !important; font-size: .82rem !important; line-height: 1.35; color: #9298a3 !important; }
div[class*="st-key-payee-row-"] button p strong {
  font-size: 1.1rem !important; font-weight: 750 !important; color: #E6E8EE !important;
  display: inline-block; min-width: 3.2em; margin-right: .45rem;
}
div[class*="st-key-payee-row-"] button:hover {
  background: rgba(102,103,201,.14) !important; transform: translateX(2px);
}
div[class*="st-key-payee-row-"] button:focus:not(:active) {
  border: none !important; box-shadow: none !important;
}
div[class*="st-key-payee-row-"] { border-bottom: 1px solid rgba(44,45,51,.7); }
"""


#: 群收款「参与人」区（人数徽章 + 名字芯片）
AA_PEOPLE_CSS = """
.aa-people { background: rgba(102,103,201,.07); border: 1px solid rgba(102,103,201,.22);
  border-radius: 12px; padding: .5rem .7rem; margin: .3rem 0 .2rem; }
.aa-people-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.aa-people-label { color: #9aa0ac; font-size: .8rem; letter-spacing: .3px; }
.aa-people-count { background: #2b2940; color: #b7b1ff; border-radius: 999px;
  padding: 1px 9px; font-size: .78rem; font-weight: 650; }
.aa-chips { display: flex; gap: .35rem; flex-wrap: wrap; margin-top: .35rem; }
.aa-chip { background: #1c1d21; border: 1px solid #2c2d33; border-radius: 999px;
  padding: 2px 10px; font-size: .9rem; color: #E6E8EE; }
.aa-empty { color: #9aa0ac; font-size: .82rem; margin-top: .3rem; }
/* 2026-10-31（用户要求）：参与人与每人应摊表合并成一块，并加一点色彩区分 */
.aa-merged { background: linear-gradient(180deg, rgba(102,103,201,.13), rgba(102,103,201,.05));
  border: 1px solid rgba(122,120,220,.35); }
.aa-split { margin-top: .45rem; border-top: 1px dashed rgba(122,120,220,.28); padding-top: .35rem; }
.aa-split-row { display: flex; align-items: baseline; justify-content: space-between;
  gap: .6rem; padding: .22rem .1rem; font-size: .92rem; color: #d7dae2;
  border-bottom: 1px solid rgba(255,255,255,.04); }
.aa-split-row:last-child { border-bottom: 0; }
/* 2026-10-31（用户要求）：金额一律白色，只有"我（承担）"那一行保留暖色 */
.aa-split-row .aa-split-amt { color: #eceef2; font-family: "SFMono-Regular", Consolas, monospace;
  font-size: .95rem; }
.aa-split-row.me { color: #f0d9a8; }
.aa-split-row.me .aa-split-amt { color: #f2b8a0; }
.aa-split-row.total { font-weight: 650; color: #eceef2;
  border-top: 1px solid rgba(122,120,220,.35); margin-top: .15rem; }
.aa-split-row.total .aa-split-amt { color: #ffffff; }
.aa-split-foot { display: flex; justify-content: space-between; gap: .5rem;
  margin-top: .3rem; color: #9aa0ac; font-size: .8rem; }
"""

CSS = f"<style>{CARD_CSS}{TABLE_CSS}{NUM_CSS}{WIDGET_CSS}{PAYEE_CSS}{PAYEE_ROW_CSS}{AA_PEOPLE_CSS}{REDUCED_MOTION_CSS}</style>"


def num_html(text: str, *, flash: bool = False) -> str:
    """把一段**已有**的数字文本包成等宽 span（可加一次脉冲）；不产生任何新数字。"""
    cls = "dsh-num dsh-flash" if flash else "dsh-num"
    return f'<span class="{cls}">{text}</span>'
