"""Private bill drill-down; frozen public query contracts remain unchanged."""
from tools._query_analysis import _period_bounds
from tools._query_common import ToolError, _fail, _money_facts, _ok
from tools._scene_read_core import rows, scope, transaction
from tools.schemas import ErrorCode, ToolResult


def read(period: str, category: str, group_by: str = 'category') -> ToolResult:
    """Read every matching expense, with the same ownership and period as the pie."""
    try:
        if group_by not in ('category', 'channel') or not isinstance(category, str) or not category:
            raise ValueError('Invalid grouping')
        start, end = _period_bounds(period)
        request = {'date_from': start.isoformat(), 'date_to': end.isoformat()}
        owned, _ = scope(request)
        matches = [r for r in rows(request, owned) if r['amount'] < 0 and r[group_by] == category]
        items = [{k: v for k, v in transaction(row).items() if k in
                  ('id', 'ts', 'counterparty', 'category', 'channel', 'memo', 'amount', 'amount_yuan')}
                 for row in matches]
        facts = {'period': period, 'category': category, 'count': len(items), 'items': items,
                 **_money_facts(-sum(r['amount'] for r in matches), 'amount')}
        return _ok({'items': items}, facts, '已查询所选分类的支出明细。')
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except (ValueError, TypeError, KeyError, OverflowError):
        return _fail(ErrorCode.INVALID_ARGUMENT, '账期或分类无效，请重新选择。')
