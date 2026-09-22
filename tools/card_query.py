"""工具层 T18（卡 23）：卡片清单 —— `list_cards(status=None)`。

规格 `docs/01-接口规格.md` §2 的 T18（人类已批 SPEC-CHANGE 17→18）+ §5（只读 → **L0**）。

口径（卡 23 拍板，逐条与规格 T18 行一致）：
- `status` 可选，取值域 = DDL 的四个合法状态（`normal` / `locked` / `lost` / `frozen`）；不传 = 全部。
  认不出（四值之外）→ `INVALID_ARGUMENT`，**绝不静默忽略**（否则用户以为过滤过了）。
  `frozen` 目前无生产者（T12 没有该动作），但它是库里合法状态、保留它才不会造出「合法却查不了」的怪洞。
- `data` = `{items: [{card_id, card_no_mask, type, status, credit_limit, single_limit, daily_limit}], total_count}`；
  `total_count` = **符合过滤条件的卡数**（无 `limit` 参数，故 = `len(items)`）。
- 金额一律整数分；储蓄卡没有授信额度 → `credit_limit` 为 `None`（不是 0、不编一个数）。
- 归属：只读**当前 user**（`current_user_id()` 口径），DAO 把 `user_id` 当查询条件 → 他人的卡一张都出不来。
- 铁律 8：库里只有 `card_no_mask`，**照抄**（不拼接、不补全、不猜测），完整卡号无从泄漏。

本模块只做只读；`tools/card.py`（T12 写路径）一个字都没动。
"""

from __future__ import annotations

import logging

from data._dao_core import list_cards as list_card_rows

from tools._query_common import _dao_reject, _invalid, _ok, current_user_id
from tools.card import _limit_facts
from tools.schemas import CardItem, ListCardsData, ListCardsReq, ToolResult

logger = logging.getLogger(__name__)


def _item(row: dict) -> dict:
    """`data.items` 的元素：规格冻结的 7 个键（其余列不外泄）。"""
    return {"card_id": row["id"], "card_no_mask": row["card_no_mask"], "type": row["type"],
            "status": row["status"], "credit_limit": row["credit_limit"],
            "single_limit": row["single_limit"], "daily_limit": row["daily_limit"]}


def _facts(row: dict) -> dict:
    """一张卡进事实包的内容：卡号（回执照抄它，里面的数字必须能在 facts 找到）+ 三档额度。

    `card_id` / `type` / `status` 是**非数字**标签（回执要用它换个中文说法，如 `lost` → 「已挂失」）；
    数字校验器只认数字，标签不影响校验。
    额度的分/元两份由 `tools/card._limit_facts` 生成 —— 与 T12 的写快照**同一份实现**，
    不另写一份（两份实现必然漂移）。
    """
    return {"card_id": row["id"], "card_no_mask": row["card_no_mask"],
            "type": row["type"], "status": row["status"], **_limit_facts(row)}


def list_cards(status: str | None = None) -> ToolResult:
    """T18 卡片清单（L0 只读）。`data` = `{items[], total_count}`。

    错误码：`status` 不在四值内 / 结构不合法（含非字符串）→ `INVALID_ARGUMENT`。
    空结果是**正常结果**（`ok=True`、`total_count=0`），不是错误。
    """
    if (bad := _invalid(ListCardsReq, status=status)) is not None:
        return bad
    try:
        rows = list_card_rows(current_user_id(), status)
    except ValueError as exc:
        return _dao_reject(exc)
    items = [_item(row) for row in rows]
    facts = {"total_count": len(items), "items": [_facts(row) for row in rows]}
    data = ListCardsData(items=[CardItem(**item) for item in items], total_count=len(items))
    message = ("该用户名下没有符合条件的卡片。" if not items
               else f"共 {facts['total_count']} 张卡。")
    return _ok(data.model_dump(), facts, message)
