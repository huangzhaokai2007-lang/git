"""F18 订阅提醒的回执渲染。

**为什么单独一个文件**：`agent/render_lists.py` 已 291 行（红线 300），加不进约 20 行。
按本仓先例（`_write_wealth.py` / `_risk_questions.py`）拆出，`templates.py` 直接 import。

口径：数字全部来自 facts（铁律 2）；空结果走独立句子、不套列表头（与 `render_cards` /
`render_subscriptions` 同口径）。
"""

from __future__ import annotations

from typing import Mapping

from agent.render_lists import TemplateError

T_REMIND_EMPTY = "未来 {within_days} 天内没有要扣费的订阅。"
T_REMIND_HEAD = "未来 {within_days} 天内有 {due_count} 个订阅要扣费，合计 {total_amount_yuan} 元："


def remind_line(item: Mapping) -> str:
    """一行提醒：商户、金额、还有几天、具体日期。数字与日期全部照抄 facts。"""
    return (f"- {item['merchant']}：{item['amount_yuan']} 元，"
            f"{item['days_cn']}（{item['next_charge_date']}）")


def render_reminders(facts: Mapping) -> str:
    """`subscription_remind` 回执：空结果优先，再汇总句 + 明细。"""
    if not facts["items"]:
        return T_REMIND_EMPTY.format(**facts)
    try:
        head = T_REMIND_HEAD.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'subscription_remind' 模板缺少 facts 键：{exc}") from exc
    return "\n".join([head, *(remind_line(item) for item in facts["items"])])
