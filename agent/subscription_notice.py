"""F18 订阅提醒的**惰性触达**：随用户下一次交互带出，不启后台进程。

与 `agent/scheduled_runner.due_soon_notices` 同一套口径（该模块是仓库里已有的先例）：

- **惰性**：没有 cron / MQ / 长连接，所以不叫"推送"，只在下一次 `orchestrator.handle` 时
  把提醒拼到回执前面；
- **去重**：每笔订阅**每个扣费日**只提醒一次（key = `subscription_id:next_charge_date`）。
  同一期反复提会变成噪音，而下一期换了日期就应当重新提醒 —— 所以 key 必须带日期；
- **故障隔离**：取数失败只记日志，**绝不影响本次请求**（与结算口径一致）；
- **不抢答**：本轮用户就是在问"有什么要扣费的"，就不再叠一句提醒。

去重状态是**进程内**的（`_REMINDED`），与测评结果、确认凭证同族：本 demo 不持久化提醒状态，
重启后重新提醒一次是可接受的（宁可多提一次，也不要漏提）。
"""

from __future__ import annotations

import logging

from tools import subscription_remind

logger = logging.getLogger(__name__)

#: 提前多久提醒。与工具缺省一致（`tools.subscription_remind.DEFAULT_WITHIN_DAYS`）：
#: 太早用户记不住，太晚来不及取消。
REMIND_WITHIN_DAYS = 7

#: 侧边栏余额刷新借用了 `handle`，但它不是用户对话 —— 提醒不能混进余额文案里。
NON_CHAT_SUFFIX = ":sidebar-balance"

#: session_id → 已提醒过的 `subscription_id:扣费日`
_REMINDED: dict[str, set[str]] = {}


def _line(item: dict) -> str:
    """一条提醒（数字与日期全部照抄 facts，本层不做任何换算）。"""
    return (f"【订阅提醒】{item['merchant']} {item['days_cn']}"
            f"（{item['next_charge_date']}）将扣费 {item['amount_yuan']} 元；")


def notices(session_id: str, intent: str | None = None) -> list[str]:
    """本会话**尚未提醒过**的到期订阅，每条一句；没有就返回空列表（不写任何表）。"""
    if intent == "subscription_remind":
        return []                                    # 本轮就是在查订阅，别叠一句
    if str(session_id).endswith(NON_CHAT_SUFFIX):
        return []                                    # 侧边栏余额刷新，不是对话
    try:
        result = subscription_remind.list_due_subscriptions(REMIND_WITHIN_DAYS)
    except Exception:                                # noqa: BLE001 —— 提醒故障不拖垮请求
        logger.exception("订阅提醒：取到期订阅失败，本轮跳过")
        return []
    if not result.ok:
        return []
    seen = _REMINDED.setdefault(session_id, set())
    lines: list[str] = []
    for item in result.facts.get("items") or []:
        key = f"{item['subscription_id']}:{item['next_charge_date']}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(_line(item))
    return lines


def reset(session_id: str | None = None) -> None:
    """清空去重状态（测试逐用例复位；传 None 清全部）。"""
    if session_id is None:
        _REMINDED.clear()
    else:
        _REMINDED.pop(session_id, None)
