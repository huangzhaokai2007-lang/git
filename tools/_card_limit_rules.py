"""F06 确定性业务规则；不依赖 LLM，不生成资金。"""
from __future__ import annotations

from data import card_limit as store
from data.card_limit_seed import MAX_CREDIT, MAX_DAILY
from tools._query_common import _money

KINDS = {"fixed": "固定授信额度", "temporary": "临时授信额度", "daily": "单日交易限额"}
STATUSES = {"pending": "审核中", "approved": "已通过并生效", "rejected": "未通过"}


def snapshot(conn, row: dict) -> dict:
    temp = store.temporary(conn, row["account_id"])
    account = store.credit_account(conn, row["account_id"])
    return {"fixed": row["credit_limit"], "daily": row["daily_limit"], "status": row["status"],
            "single": row["single_limit"], "account_status": account["status"],
            "available": account["available"], "balance": account["balance"],
            "account_id": row["account_id"], "temporary": temp}


def effective(row: dict, temp: dict | None) -> int:
    return max(row["credit_limit"] or 0, temp["total_limit"] if temp else 0)


def validate(conn, row: dict, kind: str, target: int, days: int | None) -> str | None:
    account = store.credit_account(conn, row["account_id"])
    if row["status"] != "normal" or account["status"] != "active":
        return "卡片或关联账户状态不支持调整。"
    if kind != "daily" and (row["type"] != "credit" or row["credit_limit"] is None):
        return "储蓄卡不支持信用授信调整，请选择单日交易限额。"
    if kind == "temporary" and (days is None or target <= effective(row, store.temporary(conn, row["account_id"]))):
        return "临时额度需填写有效期限，且高于当前有效授信额度。"
    if kind != "temporary" and days is not None:
        return "该调整类型无需填写临时有效期。"
    cap = MAX_DAILY if kind == "daily" else MAX_CREDIT
    if not 0 < target <= cap:
        return "申请额度超出模拟允许范围。"
    if kind == "fixed":
        occupied = max(-account["balance"], effective(row, store.temporary(conn, row["account_id"])) - account["available"], 0)
        if target < occupied:
            return "申请固定额度低于已用或冻结占用额度，不能降低到该金额。"
    return None


def card_view(conn, row: dict, kind: str) -> dict:
    temp = store.temporary(conn, row["account_id"])
    current = row["daily_limit"] if kind == "daily" else effective(row, temp) if kind == "temporary" else row["credit_limit"]
    reason = validate(conn, row, kind, MAX_CREDIT, 1 if kind == "temporary" else None)
    return {"card_id": row["id"], "card_no_mask": row["card_no_mask"], "type": row["type"],
            "status": row["status"], "eligible": reason is None, "reason": reason or "",
            "current_limit": current, "current_limit_yuan": _money(current) if current is not None else "—",
            "fixed_limit_yuan": _money(row["credit_limit"]) if row["credit_limit"] is not None else "—",
            "effective_limit_yuan": _money(effective(row, temp)),
            "max_limit_yuan": _money(MAX_DAILY if kind == "daily" else MAX_CREDIT)}


def application_view(item: dict, now: str) -> dict:
    return {**item, "kind_cn": KINDS[item["kind"]], "status_cn": STATUSES[item["status"]],
            "old_limit_yuan": _money(item["old_limit"]), "target_yuan": _money(item["target"]),
            "approved_limit_yuan": _money(item["approved_limit"]) if item["approved_limit"] is not None else "—",
            "expired": bool(item["status"] == "approved" and item["expires_at"] and item["expires_at"] <= now)}
