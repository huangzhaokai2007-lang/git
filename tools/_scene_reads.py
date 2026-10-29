"""Private contextual scene reads; no public/frozen tool contracts are changed."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from tools._query_common import ToolError, _fail, _money_facts, _ok
from tools._scene_read_core import account_groups, month, rows, safe, scope, summary, transaction
from tools.schemas import ErrorCode, ToolResult

ALIASES = {"card_bills": "cards", "card_transactions": "transactions",
           "compare_periods": "comparison", "txn_detail": "detail",
           "txn_merchant_history": "transactions", "txn_refund": "refunds",
           "txn_duplicates": "duplicates", "wealth_held": "holdings",
           "wealth_maturity": "holdings", "wealth_risk_match": "products",
           "subscription_monthly_cost": "subscriptions", "subscription_upcoming": "subscriptions",
           "subscription_detail": "subscriptions", "product_detail": "products",
           "merchant_breakdown": "transactions", "txn_largest": "largest"}
MODES = {"cards", "transactions", "detail", "comparison", "duplicates", "refunds",
         "subscriptions", "holdings", "products", "card_detail", "card_limits", "payee_detail", "largest", "txn_cards"}


def _validate(request: dict) -> str:
    mode = request.get("mode", request.get("kind", "transactions"))
    mode = ALIASES.get(mode, mode)
    if mode not in MODES:
        raise ValueError("不支持的查询类型")
    for key in ("period", "compare_period"):
        if request.get(key) is not None:
            month(request[key])
    if request.get("direction") not in (None, "in", "out"):
        raise ValueError("收支方向无效")
    amount = request.get("min_amount")
    if amount is not None and (type(amount) is not int or amount < 0):
        raise ValueError("金额须为非负整数分")
    for key in ("card_id", "txn_id", "category", "counterparty", "sub_id", "product_id"):
        if key in request and (not isinstance(request[key], str) or not request[key].strip()):
            raise ValueError("查询条件无效")
    return mode


def _delegated(mode: str, request: dict) -> ToolResult | None:
    if mode == 'payee_detail':
        from tools.payee import list_payees
        result = list_payees()
        if not result.ok:
            return result
        target = request.get('payee')
        items = [r for r in result.data.get('items', []) if target in (r.get('id'), r.get('name'), r.get('payee_id'), r.get('masked_phone'))]
        if len(items) != 1:
            return _fail(ErrorCode.INVALID_ARGUMENT, '请明确选择本人收款人。')
        row = items[0]
        return _ok({'items': items}, {'items': items}, f"收款人：{safe(row['name'])}，{safe(row.get('bank', ''))}，{safe(row.get('masked_phone', ''))}。")
    if mode == "subscriptions":
        from tools.subscription import list_subscriptions
        result = list_subscriptions()
    elif mode in ("holdings", "products"):
        from tools.wealth_holdings import list_all_products, list_holdings
        result = list_holdings() if mode == "holdings" else list_all_products()
    elif mode in ("card_detail", "card_limits"):
        from tools.card_binding import card_detail
        if not request.get("card_id"):
            return _fail(ErrorCode.INVALID_ARGUMENT, "请先选择已绑定银行卡。")
        scope(request)
        result = card_detail(request["card_id"])
    else:
        return None
    if not result.ok:
        return result
    from tools._scene_read_delegates import render_delegate
    return render_delegate(mode, request, result)


def _duplicates(items: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in items:
        if row["direction"] == "out":
            groups[(row["account_id"], row["counterparty"], row["amount"], row["ts"][:10])].append(row)
    return [transaction(row) for group in groups.values() if len(group) > 1 for row in group]


def _select(mode: str, request: dict, selected: dict, bound: list[dict], items: list[dict]) -> tuple[list[dict], dict]:
    extras = {}
    if mode == "cards":
        return account_groups(items, bound, selected), extras
    if mode == "duplicates":
        return _duplicates(items), extras
    if mode == "refunds":
        items = [row for row in items if row["direction"] == "in" and
                 "退款" in " ".join(str(row.get(k) or "") for k in ("category", "memo", "counterparty"))]
    if mode == "comparison":
        if not request.get("compare_period"):
            raise ValueError("请提供对比账期")
        previous = summary(rows({**request, "period": request["compare_period"]}, selected))
        extras = {"compare_period": request["compare_period"], "previous_summary": previous,
                  **_money_facts(summary(items)["outgoing"] - previous["outgoing"], "outgoing_change")}
    return [transaction(row) for row in items], extras


def _message(mode: str, data: dict) -> str:
    total = data["summary"]
    label = "关联账户流水" if data["scope"]["card_id"] else "本人账户流水"
    text = (f"{label}：共 {total['count']} 笔，收入 {total['incoming_yuan']} 元，"
            f"支出 {total['outgoing_yuan']} 元，净额 {total['net_yuan']} 元。\n"
            "流水仅记录账户，无法直接归属到卡；共享账户只汇总一次，卡级金额未分配。")
    if mode == "cards":
        for item in data["items"]:
            masks = "、".join(safe(c["card_no_mask"]) for c in item["cards"]) or "未绑定卡的账户"
            text += (f"\n- {masks}：关联账户收入 {item['summary']['incoming_yuan']} 元，"
                     f"支出 {item['summary']['outgoing_yuan']} 元，{item['summary']['count']} 笔交易。")
    else:
        for item in data["items"]:
            text += (f"\n- 流水 {safe(item['id'])}｜{safe(item['ts'])}｜"
                     f"{safe(item['counterparty'])}｜{safe(item['category'])}｜{item['amount_yuan']} 元")
    if data.get('displayed_count', 0) < total['count'] and mode not in ('cards', 'duplicates', 'refunds'):
        text += f"\n当前展示 {data['displayed_count']} 笔，汇总覆盖所选范围全部 {total['count']} 笔交易。"
    for group in data.get('merchant_groups', []):
        text += f"\n商户 {safe(group['merchant'])}：支出 {group['outgoing_yuan']} 元。"
    if mode == "comparison":
        text += f"\n对比账期支出 {data['previous_summary']['outgoing_yuan']} 元，支出差额 {data['outgoing_change_yuan']} 元。"
    if mode == "duplicates":
        text += "\n以上仅为同账户、同日、同商户、同金额的疑似重复支出，不能据此认定重复扣款。"
    if mode == "refunds":
        text += "\n仅列出明确含退款标记的收入记录，无法确认与原消费的对应关系。"
    return text


def read(intent: str, request: dict) -> ToolResult:
    """Read trusted session ownership; request IDs never establish ownership."""
    try:
        if not isinstance(intent, str) or not isinstance(request, dict):
            raise ValueError("查询参数无效")
        request = {k: v for k, v in request.items() if v is not None}
        request['direction'] = {'income': 'in', 'expense': 'out'}.get(request.get('direction'), request.get('direction'))
        if request.get('category') == '食品餐饮':
            request['category'] = '餐饮'
        mode = _validate(request)
        if (result := _delegated(mode, request)) is not None:
            return result
        if mode not in ('detail', 'txn_cards') and not request.get("period") and not (request.get('date_from') and request.get('date_to')):
            raise ValueError("请提供有效账期")
        if mode in ('detail', 'txn_cards') and not request.get("txn_id"):
            raise ValueError("请提供流水标识")
        selected, bound = scope(request)
        if mode == 'txn_cards':
            source = rows(request, selected)
            if not source:
                return _fail(ErrorCode.FORBIDDEN, '该交易不在本人可查看范围内。')
            associated = [{'card_id': c['id'], 'card_no_mask': c['card_no_mask'],
                           'type': c['type'], 'status': c['status']} for c in bound
                          if c['account_id'] == source[0]['account_id']]
            data = {'items': associated, 'cards': associated, 'txn_id': source[0]['id'],
                    'scope': selected, 'direct_card_attribution': False}
            text = '流水仅记录关联账户，无法确认实际付款卡。'
            text += ('已核验该账户的本人已绑定银行卡：' + '、'.join(safe(c['card_no_mask']) for c in associated)
                     if associated else '该账户没有本人已绑定银行卡。')
            return _ok(data, data, text)
        if request.get('txn_id') and mode != 'detail':
            source = rows({'txn_id': request['txn_id']}, selected)
            if not source:
                return _fail(ErrorCode.FORBIDDEN, '该交易不在本人可查看范围内。')
            if request.get('kind') in ('txn_merchant_history', 'txn_refund'):
                request['counterparty'] = source[0]['counterparty']
            request.pop('txn_id')
        raw = rows(request, selected)
        if mode == 'largest':
            raw = sorted(raw, key=lambda row: (-abs(row['amount']), row['ts'], row['id']))[:1]
            if not raw:
                data = {'items': [], 'scope': selected, 'period': request.get('period'), 'mode': mode}
                return _ok(data, data, '当前所选范围没有符合条件的交易，请更换月份或分类。')
        if request.get('large_only'):
            raw = sorted(raw, key=lambda row: abs(row['amount']), reverse=True)
        if mode == "detail" and not raw:
            return _fail(ErrorCode.FORBIDDEN, "该流水不在可查看的账户范围内。")
        return _result(mode, request, selected, bound, raw)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except (ValueError, TypeError, OverflowError):
        return _fail(ErrorCode.INVALID_ARGUMENT, "查询条件无效，请提供有效账期、筛选条件及整数分金额。")
    except sqlite3.Error:
        return _fail(ErrorCode.INVALID_STATE, "暂时无法查询，请稍后重试。")


def _result(mode: str, request: dict, selected: dict, bound: list[dict], raw: list[dict]) -> ToolResult:
    items, extras = _select(mode, request, selected, bound, raw)
    totals = summary(raw if mode not in ("duplicates", "refunds") else items)
    if request.get('rank') and mode == 'cards':
        items.sort(key=lambda row: row['summary']['outgoing'], reverse=True)
    merchant_groups = []
    if request.get('kind') == 'merchant_breakdown':
        merchants = {row['counterparty'] for row in raw if row['direction'] == 'out'}
        merchant_groups = [{'merchant': merchant, **summary([r for r in raw if r['counterparty'] == merchant])}
                           for merchant in merchants]
        merchant_groups.sort(key=lambda row: row['outgoing'], reverse=True)
    displayed = items if mode == 'cards' else items[:50]
    data = {"items": items, "scope": selected, "period": request.get("period"),
            "summary": totals, "mode": mode, "truncated": False,
            "cards": [{"card_id": c["id"], "card_no_mask": c["card_no_mask"]} for c in bound], **extras}
    data.update(items=displayed, displayed_count=len(displayed), merchant_groups=merchant_groups)
    return _ok(data, data, _message(mode, data))
