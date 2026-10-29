"""群收款的编排层**只读**入口（界面用）：列进行中 / 全部 群收款请求。

界面（`interfaces/web/`）只能调 `agent/`，不能直连 `tools/`；本模块把 T42 包一层给卡面用。
**只读、无副作用**（不登记、不撤销、不改状态）。
"""

from __future__ import annotations


def requests(status: str | None = None) -> list[dict]:
    """群收款请求列表（`status=None` = 全部）。失败 → 空列表（卡面不画）。"""
    from tools import _aa_request                       # 延迟 import：避免顶层循环
    listed = _aa_request.list_aa_requests(status)
    if not listed.ok:
        return []
    return [dict(item) for item in (listed.facts.get("items") or [])]


def active() -> list[dict]:
    """进行中的群收款（卡面只对这几笔给操作按钮）。"""
    return requests("active")


def candidates() -> list[str]:
    """可选参与人（排除黑名单；黑名单不能收款，口径见人类原文）。"""
    from tools import payee as _payee          # 延迟 import：避免顶层循环

    listed = _payee.list_payees()
    if not listed.ok:
        return []
    return [str(item["name"]) for item in (listed.facts or {}).get("items", [])
            if not item.get("blacklist")]


def group_plan(names: list[str], total_yuan: float) -> dict:
    """含我的群收款分摊方案：每人 / 我承担 / 待收（数字全部来自 `tools`，界面不自己算）。"""
    from tools._aa_split import split_with_self

    people = [str(name).strip() for name in names if str(name).strip()]
    total_cents = int(round(float(total_yuan or 0) * 100))
    plan = split_with_self(total_cents, len(people))
    return {
        "people": people,
        "count": len(people) + 1,                      # 含我
        "total_yuan": f"{total_cents / 100:.2f}",
        "per_yuan": f"{plan['per_cents'] / 100:.2f}",
        "mine_yuan": f"{plan['mine_cents'] / 100:.2f}",
        "collect_yuan": f"{plan['collect_cents'] / 100:.2f}",
        "collect_cents": plan["collect_cents"],
    }
