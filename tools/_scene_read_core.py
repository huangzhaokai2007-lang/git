"""Private scene read storage, integer summaries and safe display helpers."""
from __future__ import annotations

import html
import re
from datetime import date, timedelta

from data import card_binding as store, dao
from tools._query_common import ToolError, _money_facts, current_user_id
from tools.schemas import ErrorCode


def safe(value: object) -> str:
    return html.escape(str(value)).replace("|", "／").replace("\n", " ").replace("`", "｀")


def month(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("账期须为有效年月")
    start = date.fromisoformat(value + "-01")
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def cards() -> list[dict]:
    conn = dao.connection()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='agent_card_binding'").fetchone():
        return []
    return store.bound_cards(conn, current_user_id(), None)


def scope(request: dict) -> tuple[dict, list[dict]]:
    bound = cards()
    selected = request.get("card_id")
    match = next((card for card in bound if card["id"] == selected), None)
    if selected is not None and match is None:
        raise ToolError(ErrorCode.FORBIDDEN, "该银行卡未绑定或不可查看。")
    ids = ([match["account_id"]] if match else [r["id"] for r in dao.connection().execute(
        "SELECT id FROM account WHERE user_id=? ORDER BY id", (current_user_id(),))])
    return {"kind": "associated_account" if match else "owned_accounts", "account_ids": ids,
            "card_id": selected, "direct_card_attribution": False}, bound


def rows(request: dict, selected: dict) -> list[dict]:
    where, args = ["a.user_id=?"], [current_user_id()]
    if not selected["account_ids"]:
        return []
    where.append("t.account_id IN (" + ",".join("?" for _ in selected["account_ids"]) + ")")
    args.extend(selected["account_ids"])
    if request.get('date_from') and request.get('date_to'):
        start, end = date.fromisoformat(request['date_from']), date.fromisoformat(request['date_to'])
        if end < start:
            raise ValueError('日期范围无效')
        where.extend(['t.ts>=?', 't.ts<?'])
        args.extend([start.isoformat(), (end + timedelta(days=1)).isoformat()])
    elif request.get("period"):
        start, end = month(request["period"])
        where.extend(["t.ts>=?", "t.ts<?"])
        args.extend([start, end])
    for key in ("category", "counterparty", "direction"):
        if request.get(key) is not None:
            where.append(f"t.{key}=?")
            args.append(request[key])
    if request.get("txn_id"):
        where.append("t.id=?")
        args.append(request["txn_id"])
    if request.get("min_amount") is not None:
        where.append("ABS(t.amount)>=?")
        args.append(request["min_amount"])
    return [dict(row) for row in dao.connection().execute(
        "SELECT t.* FROM txn t JOIN account a ON a.id=t.account_id WHERE " +
        " AND ".join(where) + " ORDER BY t.ts DESC,t.id DESC", args)]


def summary(items: list[dict]) -> dict:
    incoming = sum(row["amount"] for row in items if row["direction"] == "in")
    outgoing = sum(-row["amount"] for row in items if row["direction"] == "out")
    return {"count": len(items), **_money_facts(incoming, "incoming"),
            **_money_facts(outgoing, "outgoing"), **_money_facts(incoming - outgoing, "net")}


def transaction(row: dict) -> dict:
    return {**row, **_money_facts(row["amount"], "amount"),
            **_money_facts(row["balance_after"], "balance_after")}


def account_groups(items: list[dict], bound: list[dict], selected: dict) -> list[dict]:
    result = []
    for account in selected["account_ids"]:
        matches = [card for card in bound if card["account_id"] == account]
        result.append({"account_id": account, "cards": [
            {"card_id": c["id"], "card_no_mask": c["card_no_mask"]} for c in matches],
            "label": "关联账户汇总（无法直接归属到卡）", "unallocated": True,
            "summary": summary([row for row in items if row["account_id"] == account])})
    return result
