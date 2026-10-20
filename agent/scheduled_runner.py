"""定时转账的**惰性结算器**（F16 · Q1 / Q9）。

**为什么不启后台调度器**（F16 设计 Q1，人类已批）：全项目没有任何调度设施。后台线程要给
Streamlit / FastAPI / CLI 三种入口各起一套，而 CLI 是**一次性进程**（跑完就退），定时器随之消失；
引入 APScheduler 又违反铁律 6（新依赖先问）。

于是采用**惰性结算**：每个会话轮次入口先把「已到期」的预约结算掉，再处理本次请求。

| 关注点 | 本模块怎么处理 |
|---|---|
| **开销** | 一次带索引的 `SELECT … WHERE status='pending' AND execute_at <= now`；**无到期项时不写任何表**（不能因为"顺手"就每轮写审计） |
| **幂等 / 并发** | 结算本身由 T22 的条件更新抢锁保证；**重启恢复**天然成立（`pending` 行还在库里，下轮继续） |
| **不与分类耦合**（Q9） | 本模块只做「查到期 + 调 T22 + 拼告知句」，不碰状态机、不改 slots |
| **告知**（验收第 3 条） | 结算结果拼成句子返回，由 `orchestrator.handle` 附在本次回执**前面** —— 用户下次交互就能看到「到期执行成功/失败」 |
| **失败不拖垮请求** | 结算异常只记日志、本轮跳过；**绝不让预约的故障影响用户当前的正常请求** |

**已知限制**：到期时刻与用户下次交互之间有空窗 —— 用户不打开应用，预约就不会执行。
这是 Q1 明确接受的取舍（demo 期时间锚是 `data.seed.AS_OF`，本就非真实时钟）。
"""

from __future__ import annotations

import logging

from data import dao
from tools import _transfer_schedule, transfer

logger = logging.getLogger(__name__)

#: 每轮最多结算几条（防御：避免一次把几百条都扣了；多余的留到下一轮）
MAX_PER_TURN = 5


def _due_ids(limit: int) -> list[str]:
    """已到期、仍是 `pending` 的预约 id（**只读**；`execute_at` 是 ISO 文本，字典序即时间序）。"""
    conn = dao.connection()
    rows = conn.execute(
        "SELECT id FROM scheduled_transfer WHERE status = 'pending' AND execute_at <= ?"
        " ORDER BY execute_at, id LIMIT ?",
        (transfer._now().isoformat(timespec="minutes"), limit)).fetchall()
    return [row["id"] for row in rows]


def settle_due() -> list[str]:
    """结算已到期的预约，返回给用户的**告知句**；无到期项 → 空列表且**不写任何表**。"""
    try:
        due = _due_ids(MAX_PER_TURN)
    except Exception:                                     # noqa: BLE001 —— 结算故障绝不能拖垮本次请求
        logger.exception("惰性结算：查询到期预约失败，本轮跳过")
        return []
    notices: list[str] = []
    for schedule_id in due:
        try:
            result = _transfer_schedule.execute_scheduled(schedule_id)
        except Exception:                                 # noqa: BLE001
            logger.exception("惰性结算：结算预约 %s 失败", schedule_id)
            continue
        tag = "【预约转账已执行】" if result.ok else "【预约转账未执行】"
        notices.append(f"{tag}{result.message}")
    return notices
