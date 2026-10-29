"""风险卡片：未测评给入口，测完给**结果卡**（总分 / 等级 / 逐题得分 / 可买范围）。

为什么单独一个模块：`wealth_ui` 管的是产品卡，这里是「人」的卡片，职责不同；
而且它只依赖 `intent` + facts，不该被塞进理财模块里。

**卡片上不出现任何一个自己算出来的数字**：总分、每题得分、可买范围、有效期、等级说明
全部来自工具层 facts（铁律 2）。界面只做排版与翻中文短名。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import streamlit as st

from agent.risk_flow import QUESTION_KEYS_CN


def _scores_table(rows: list[Mapping[str, Any]]) -> None:
    """逐题得分明细（题名短名翻中文；短名的唯一来源是 agent 层）。"""
    st.markdown(
        "| 题目 | 得分 |\n|---|---|\n" + "\n".join(
            f"| {QUESTION_KEYS_CN.get(str(row.get('key')), str(row.get('key')))} | "
            f"{row.get('score')} 分 |" for row in rows))


def render_result(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                  key_prefix: str) -> None:
    """测评完成后的结果卡：`total_score` 是它存在的标志（查询等级时没有这一项）。

    卡片要说清四件事：**得了多少分 → 是什么等级 → 能买什么 → 这份结果管到哪天**。
    """
    facts = turn.get('facts') or {}
    if facts.get('total_score') is None:
        return
    st.markdown('#### 测评结果')
    st.markdown(f"**{facts.get('risk_label')}（{facts.get('risk_level')}）** · "
                f"{facts.get('total_score')} / {facts.get('max_score')} 分"
                f"（共 {facts.get('question_count')} 题）")
    if facts.get('risk_desc'):
        st.markdown(f"> {facts['risk_desc']}")
    st.markdown(f"可买范围：**{facts.get('buyable_text')}**（等级不超过您承受能力的产品）"
                f" · 有效期至 {facts.get('valid_until')}")

    rows = [row for row in (facts.get('score_detail') or [])]
    if rows:
        with st.expander(f'逐题得分（{len(rows)} 题 · 合计 {facts.get("total_score")} 分）',
                         expanded=False):
            _scores_table(rows)
            st.caption("合计由工具层计算，界面只照抄 —— 分数口径在 `tools/_wealth_risk`。")
    if st.button('看看适合我的产品 →', key=f'{key_prefix}-recommend',
                 type='primary', width='stretch'):
        ask('推荐理财产品')


def render_turn(turn: Mapping[str, Any], *, ask: Callable[[str], None],
                key_prefix: str = "risk") -> None:
    """`risk_assess` 的卡片：测完了给结果，没测评给入口。"""
    if turn.get('intent') != 'risk_assess':
        return
    facts = turn.get('facts') or {}
    if facts.get('total_score') is not None:
        render_result(turn, ask=ask, key_prefix=key_prefix)
        return
    if facts.get('assessed') is not False:
        return
    #: 过期和没做过是两件事：过期的用户已经做过一遍，别让他以为按钮是"从头再来"。
    expired = facts.get('expired') is True
    st.markdown('#### 测评已过期，重新做一次' if expired else '#### 先做一次风险测评')
    st.markdown('一共 15 道题、大约 3 分钟，做完我就能按您的承受能力推荐产品。')
    label = '重新测评' if expired else '开始风险测评'
    if st.button(label, key=f'{key_prefix}-assess', type='primary', width='stretch'):
        ask('做风险测评')
