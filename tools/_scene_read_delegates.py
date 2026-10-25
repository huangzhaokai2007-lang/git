"""Presentation facts for contextual subscription, card and holding reads."""
from datetime import date, timedelta

from data import dao
from tools._query_common import _fail, _money_facts, _ok, current_user_id
from tools._scene_read_core import safe
from tools.schemas import ErrorCode


def _pick(items: list[dict], request: dict, fields: tuple[str, ...]) -> list[dict]:
    target = request.get('sub_id') or request.get('product_id') or request.get('product')
    return [row for row in items if any(str(row.get(k)) == str(target) for k in fields)] if target else items


def _subscriptions(request: dict, result):
    items = _pick((result.facts or {}).get('items', []), request, ('id', 'sub_id'))
    if request.get('sub_id') and not items:
        return _fail(ErrorCode.FORBIDDEN, '该订阅不在本人可查看范围内。')
    items = sorted(items, key=lambda row: (row['next_charge_date'], row['id']))
    monthly = sum(row['amount'] for row in items if row['cycle'] == 'monthly')
    yearly = sum(row['amount'] for row in items if row['cycle'] == 'yearly')
    facts = {'items': items, 'months_per_year': 12, **_money_facts(monthly, 'monthly_total'),
             **_money_facts(yearly, 'yearly_total'),
             **_money_facts((monthly * 12 + yearly + 6) // 12, 'monthly_equivalent')}
    text = '\n'.join(f"- {safe(row['merchant'])}：{row['amount_yuan']} 元／"
                     f"{'每月' if row['cycle'] == 'monthly' else '每年'}，下次扣费 {row['next_charge_date']}"
                     for row in items) or '暂无符合条件的进行中订阅。'
    if request.get('kind') == 'subscription_monthly_cost':
        text += (f"\n月付合计 {facts['monthly_total_yuan']} 元，年付合计 {facts['yearly_total_yuan']} 元。"
                 f"年付按 {facts['months_per_year']} 个月折算后，月均约 {facts['monthly_equivalent_yuan']} 元；不是本月实际扣款。")
    return _ok({'items': items}, facts, text)


def _holdings(request: dict, result):
    items = _pick(result.data.get('items', []), request, ('name', 'product_id'))
    if request.get('product') and not items:
        return _ok({'items': []}, {}, '当前未查询到该产品的持有中记录；不代表历史上从未买过。')
    facts, lines = {'items': items, 'dates': []}, []
    for row in items:
        lines.append(f"- {safe(row['name'])}：持有 {row['amount_yuan']} 元，风险 {row['risk_level']}。")
        if request.get('kind') == 'wealth_maturity':
            lots = dao.connection().execute(
                "SELECT purchase_date FROM holding WHERE user_id=? AND product_id=? AND status='held'",
                (current_user_id(), row['product_id'])).fetchall()
            dates = sorted({(date.fromisoformat(lot['purchase_date']) + timedelta(days=row['term_days'])).isoformat()
                            for lot in lots})
            facts['dates'].extend(dates)
            lines.append('按各笔买入日及产品期限计算的可赎回日期：' + '、'.join(dates) + '；实际可办理状态仍需预检。')
    return _ok({'items': items}, facts, '\n'.join(lines) or '暂无持有中的理财产品。')


def render_delegate(mode: str, request: dict, result):
    if mode == 'subscriptions':
        return _subscriptions(request, result)
    if mode == 'holdings':
        return _holdings(request, result)
    if mode == 'products':
        items = _pick(result.data.get('items', []), request, ('name', 'product_id'))
        text = '\n'.join(f"- {safe(row['name'])}：风险 {row['risk_level']}，"
                         f"{'在本人测评风险等级内' if row['recommended'] else '超出本人测评风险等级'}。" for row in items)
        if request.get('kind') == 'product_detail':
            text += '\n' + '\n'.join(f"期限 {row['term']} 天，起购 {row['min_amount_yuan']} 元，"
                                     f"预期年化 {row['yield']}（不代表实际收益）。" for row in items)
        return _ok({'items': items}, {**result.facts, 'items': items}, text or '没有匹配的产品。')
    data, facts = result.data, result.facts
    lines = [f"银行卡：{data['card_no_mask']}，状态：{data['status']}。"]
    for field, label in (('credit_limit', '授信额度'), ('single_limit', '单笔限额'), ('daily_limit', '日限额')):
        if facts.get(field + '_yuan') is not None:
            lines.append(f"{label}：{facts[field + '_yuan']} 元。")
    return _ok(data, facts, '\n'.join(lines))
