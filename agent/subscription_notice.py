"""F18 订阅提醒的**常驻概览**：给界面画提醒条，回答「现在有没有要处理的」。

**为什么不再往回执里前置提醒**：试过一版「下一次交互时把提醒拼到回执最前面」，两个问题：

1. **覆盖不全** —— 只有走 `orchestrator.handle()` 的路径才会带出；表单提交、卡片上的
   直接操作（锁卡/取消订阅等）都绕过它，用户在这些场景永远看不到提醒。
2. **污染回执** —— 每条回执开头都被塞一句提醒，既有测试里大量「回执以…开头 / 等于…」
   的断言全部失效，等于用别人的测试稳定性换一次提示。

改成**界面常驻提醒条**：`summary()` 只读、不去重、不写状态，页面每帧都能问；
只要还有到期或逾期订阅就一直显示，任何操作之后都看得到。

与工具层的分工：窗口口径（7 天、闭区间、只查 active、逾期单列）全在
`tools/subscription_remind.py`，本模块只做「取一次 + 摘出概览数字」。
"""

from __future__ import annotations

import logging

from tools import subscription_remind

logger = logging.getLogger(__name__)

#: 提前多久提醒。与工具缺省一致（`tools.subscription_remind.DEFAULT_WITHIN_DAYS`）：
#: 太早用户记不住，太晚来不及取消。
REMIND_WITHIN_DAYS = 7

#: 侧边栏余额刷新借用了 `handle`，但它不是用户对话 —— 提醒条不该为它触发。
NON_CHAT_SUFFIX = ":sidebar-balance"


def summary(session_id: str) -> dict:
    """本会话的订阅概览；没有要处理的就返回空字典。

    返回 `{due, overdue, within_days, total_amount_yuan, overdue_total_amount_yuan}`，
    数字全部来自工具层 facts，界面只排版。
    """
    if str(session_id).endswith(NON_CHAT_SUFFIX):
        return {}
    try:
        result = subscription_remind.list_due_subscriptions(REMIND_WITHIN_DAYS)
    except Exception:                                # noqa: BLE001 —— 提醒故障不拖垮页面
        logger.exception("订阅提醒：取概览失败，本帧跳过")
        return {}
    if not result.ok:
        return {}
    facts = result.facts or {}
    due, overdue = int(facts.get("due_count") or 0), int(facts.get("overdue_count") or 0)
    if not due and not overdue:
        return {}
    return {"due": due, "overdue": overdue, "within_days": facts.get("within_days"),
            "total_amount_yuan": facts.get("total_amount_yuan"),
            "overdue_total_amount_yuan": facts.get("overdue_total_amount_yuan")}
