"""Deterministic, side-effect-free contextual followup planner.

Only existing classifier slots cross the intent boundary. Extra read selectors are
internal requests; callers must validate them and obtain all facts from tools.
"""
from __future__ import annotations

import calendar
import re

from agent.classifier import SLOT_SCHEMA
from agent.period import anchor_period, resolve_period
from agent.scene_phrases import ALIASES, extra

_BILLS = {'txn_query', 'bill_analysis', 'bill_report', 'anomaly_check'}
_CARDS = {'card_query', 'card_lock', 'card_unlock', 'card_report_lost', 'card_limit_adjust'}
_SUBS = {'subscription_list', 'subscription_cancel', 'subscription_remind'}
_WEALTH = {'wealth_holdings', 'wealth_all_products', 'wealth_buy', 'wealth_redeem', 'wealth_recommend'}
_NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6,
            '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12}
_ID = {'card': 'card_id', 'txn': 'txn_id', 'subscription': 'sub_id',
       'payee': 'payee', 'product': 'product'}


def _out(intent: str, slots: dict | None = None, **extra: object) -> dict:
    return {'intent': intent, 'slots': {k: v for k, v in (slots or {}).items()
                                      if k in SLOT_SCHEMA[intent]}, **extra}


def _domain(context: dict) -> str | None:
    intent = context.get('intent')
    if intent in _CARDS:
        return 'card'
    if intent in _BILLS:
        return 'txn'
    if intent in _SUBS:
        return 'subscription'
    if intent in _WEALTH:
        return 'product'
    if intent in {'payee_list', 'payee_whitelist', 'transfer_single', 'transfer_scheduled'}:
        return 'payee'
    return None


def _entity(text: str, context: dict, domain: str) -> object | None:
    rows = context.get('candidates', {}).get(domain, [])
    suffix = re.search(r'尾号\s*(\d{4})', text)
    ordinal = re.search(r'第([一二两三四五六七八九十\d]+)(?:个|张|笔|款)', text)
    if suffix:
        rows = [r for r in rows if str(r.get('last4', r.get('tail', r.get('masked_card_no',
                r.get('card_number_masked', r.get('card_no_mask', '')))))).endswith(suffix[1])]
    elif ordinal:
        value = ordinal[1]
        index = int(value) if value.isdigit() else _NUMBERS.get(value, 0)
        rows = rows[index - 1:index] if 0 < index <= len(rows) else []
    elif _domain(context) == domain and context.get(_ID[domain]):
        return context[_ID[domain]]
    if len(rows) != 1:
        return None
    row = rows[0]
    key = _ID[domain]
    if domain in {'product', 'payee'}:
        return row.get(key) or (row.get('masked_phone') if domain == 'payee' else None) or row.get('name') or row.get('id')
    return row.get(key) or row.get('id')


def _period(text: str) -> str | None:
    text = re.sub(r'^(?:那|那么|再|查一下|查询|查|看看|看一下|看)', '', text)
    text = re.sub(r'(?:的)?(?:账单|消费|交易|支出)?(?:怎么样|呢|如何|比呢)?$', '', text)
    text = re.sub(r'^和|^跟', '', text)
    if value := resolve_period(text):
        return value
    match = re.fullmatch(r'(?:(\d{4})年)?([一二三四五六七八九十]+|\d{1,2})月', text)
    if not match:
        return None
    month = int(match[2]) if match[2].isdigit() else _NUMBERS.get(match[2], 0)
    return f'{match[1] or anchor_period()[:4]}-{month:02d}' if 1 <= month <= 12 else None


def _time(text: str, context: dict) -> dict | None:
    period = _period(text)
    if not period:
        return None
    previous = context.get('intent')
    if re.match(r'^(和|跟)', text) and previous in _BILLS:
        base = resolve_period(context.get('period'))
        if not base:
            return _out(previous, ask='请先指定要比较的原账期。')
        return _out(previous, {'period': base}, read_request={
            'kind': 'compare_periods', 'period': base, 'compare_period': period})
    if previous in _CARDS:
        return _out('card_query', read_request={'kind': 'card_bills', 'period': period,
                    **({'card_id': context['card_id']} if context.get('card_id') else {})})
    if previous not in _BILLS and not re.search(r'账单|消费|交易|支出', text):
        return None
    #: 意图归属（2026-10-26 修）：原实现无条件 `'bill_report' if '账单' in text`，
    #: 于是**上轮是账单分析（bill_analysis）、这轮换个账期说「那 5 月的账单」时，继承来的意图被
    #: 硬写成 bill_report** —— 用户问的是分析，拿到的是报告。故：**有前序账单意图就继承**；
    #: 没有前序时，「X月的账单」按开宗明义要账单处理 = bill_report（与
    #: `tests/test_scene_integration.py` 锁定的口径一致，不动它）。
    intent = previous if previous in _BILLS else 'bill_report'
    if intent not in _BILLS:
        intent = 'bill_analysis'
    if intent == 'txn_query' and len(period) == 7:
        year, month = map(int, period.split('-'))
        return _out(intent, {'date_from': period + '-01',
                            'date_to': f'{period}-{calendar.monthrange(year, month)[1]:02d}'})
    return _out(intent, {'period': period})


def _navigation(text: str, context: dict) -> dict | None:
    actions = {'算了': 'cancel', '算了吧': 'cancel', '取消刚才那个': 'cancel',
               '重新来': 'reset', '重新开始': 'reset', '返回刚才的列表': 'back',
               '返回列表': 'back', '回到列表': 'back', '再试一次': 'retry', '重试': 'retry'}
    if text not in actions:
        return None
    intent = context.get('intent')
    return _out(intent if intent in SLOT_SCHEMA else 'smalltalk', action=actions[text])


def _amend(text: str, context: dict) -> dict | None:
    intent = context.get('intent')
    if intent not in {'transfer_single', 'transfer_scheduled', 'wealth_buy', 'wealth_redeem'}:
        return None
    amount = re.fullmatch(r'(?:把)?(?:金额)?改成(?:金额)?\s*(\d+(?:\.\d{1,2})?)元?', text)
    payee = re.fullmatch(r'(?:把)?收款人改成(.{1,40})', text)
    if not amount and not (payee and intent.startswith('transfer_')):
        return None
    slots = dict(context.get('slots', {}))
    slots.update({'amount': float(amount[1])} if amount else {'payee': payee[1].strip()})
    return _out(intent, slots, action='amend')


def _card(text: str, context: dict) -> dict | None:
    reference = r'(?:这张(?:卡)?|尾号\d{4}(?:的卡)?|第[一二两三四五六七八九十\d]+张(?:卡)?)'
    writes = [(rf'(?:锁卡|锁住?{reference}|(?:把)?{reference}锁住?|冻结{reference})', 'card_lock'),
              (rf'(?:解锁(?:{reference})?|(?:把)?{reference}解锁|解冻{reference})', 'card_unlock'),
              (rf'(?:挂失(?:{reference})?|(?:把)?{reference}挂失|{reference}(?:被偷了|丢了|被盗了))',
               'card_report_lost')]
    for pattern, intent in writes:
        if re.fullmatch(pattern, text):
            card = _entity(text, context, 'card')
            return _out(intent, {'card_id': card}) if card else _out(intent, ask='请明确选择要操作的银行卡。')
    if _domain(context) == 'card' and re.fullmatch(r'(?:这张卡的?)?(?:额度|限额)(?:呢|是多少|多少)?', text):
        card = _entity(text, context, 'card')
        return _out('card_query', read_request={'kind': 'card_limits', 'card_id': card}) if card else _out(
            'card_query', ask='请先选择要查看额度的银行卡。')
    return None


def _txn(text: str, context: dict) -> dict | None:
    if _domain(context) != 'txn':
        return None
    filters = {'食品餐饮': {'category': '食品餐饮'}, '餐饮占多少': {'category': '食品餐饮'},
               '只看收入': {'direction': 'income'}, '只看支出': {'direction': 'expense'},
               '大额': {'large_only': True}, '大额呢': {'large_only': True},
               '只看大额': {'large_only': True}}
    if text in filters:
        request = {'kind': 'transactions', **filters[text]}
        if context.get('period'):
            request['period'] = context['period']
        return _out('txn_query', {'category': request['category']} if 'category' in request else {},
                    read_request=request)
    category = re.fullmatch(r'(?:展开|只看|看看)?(餐饮|交通|电商|生活缴费|转账|订阅|工资|理财)(?:占多少|的明细|交易|呢)?', text)
    if category:
        return _out('txn_query', {'category': category[1]}, read_request={
            'kind': 'transactions', 'period': context.get('period'), 'category': category[1]})
    cases = [(r'(?:这个|这家)商户(?:以前|之前|历史)(?:的)?(?:记录|交易)', 'txn_merchant_history'),
             (r'(?:这笔)?(?:疑似重复|是不是重复扣款|重复扣款了吗)', 'txn_duplicates'),
             (r'(?:这笔)?(?:退款|退了没|退款了吗|我要退款)', 'txn_refund')]
    for pattern, kind in cases:
        if re.fullmatch(pattern, text):
            txn = _entity(text, context, 'txn')
            if not txn:
                return _out('txn_query', ask='请先选择具体交易。')
            extra = {'ask': '当前不支持直接发起退款；可核查该笔交易的退款记录。'} if kind == 'txn_refund' else {}
            return _out('txn_query', read_request={'kind': kind, 'txn_id': txn}, **extra)
    return None


def _subscription(text: str, context: dict) -> dict | None:
    if _domain(context) != 'subscription':
        return None
    kinds = {'每月多少钱': 'subscription_monthly_cost', '每月花多少': 'subscription_monthly_cost',
             '每月总共多少': 'subscription_monthly_cost', '下次扣款呢': 'subscription_upcoming',
             '快扣款的有哪些': 'subscription_upcoming', '展开看看': 'subscription_detail',
             '看看详情': 'subscription_detail', '这个订阅详情': 'subscription_detail'}
    sub = _entity(text, context, 'subscription')
    if re.fullmatch(r'(?:取消|退订)(?:这个|这项|第[一二三四五六七八九十\d]+个)?(?:订阅)?', text):
        return _out('subscription_cancel', {'sub_id': sub}) if sub else _out(
            'subscription_cancel', ask='请先选择要取消的订阅。')
    if text in kinds:
        kind = kinds[text]
        if kind == 'subscription_detail' and not sub:
            return _out('subscription_list', ask='请先选择要查看的订阅。')
        return _out('subscription_list', read_request={'kind': kind, **({'sub_id': sub} if sub else {})})
    return None


def _wealth(text: str, context: dict) -> dict | None:
    if _domain(context) != 'product':
        return None
    kinds = {'什么时候到期': 'wealth_maturity', '到期日呢': 'wealth_maturity',
             '我持有吗': 'wealth_held', '我买过吗': 'wealth_held',
             '风险匹配吗': 'wealth_risk_match', '适合我的风险吗': 'wealth_risk_match'}
    product = _entity(text, context, 'product')
    if text in {'赎回一半', '卖出一半', '赎回半份'}:
        return _out('wealth_redeem', {'product': product} if product else {},
                    ask='请提供要赎回的产品和具体金额；需要先核查可赎回持仓。')
    if text in kinds:
        return _out('wealth_holdings', read_request={'kind': kinds[text], 'product': product}) if product else _out(
            'wealth_holdings', ask='请先选择具体产品。')
    return None


def _select(text: str, context: dict) -> dict | None:
    domain = _domain(context)
    if not domain:
        return None
    match = re.fullmatch(r'(?:第[一二两三四五六七八九十\d]+(?:个|张|笔|款)|尾号\d{4}(?:的卡)?|这张|这笔|展开看看)', text)
    if match:
        entity = _entity(text, context, domain)
        if entity is None:
            return _out(context['intent'], ask='请明确选择列表中的具体一项。')
        return _out(context['intent'], action='select', read_request={
            'kind': domain + '_detail', _ID[domain]: entity})
    transfer = re.fullmatch(r'给(?:他|第[一二两三四五六七八九十\d]+个(?:人)?)(?:转|转账)(\d+(?:\.\d{1,2})?)元?', text)
    if transfer and domain == 'payee':
        payee = _entity(text, context, 'payee')
        return _out('transfer_single', {'payee': payee, 'amount': float(transfer[1])}) if payee else _out(
            'transfer_single', {'amount': float(transfer[1])}, ask='请明确选择收款人。')
    return None


def plan(text: str, context: dict) -> dict | None:
    """Return an understanding-only plan; never call tools, execute or mutate context."""
    if not isinstance(text, str) or not isinstance(context, dict):
        return None
    text = text.strip().rstrip('？?。！!').strip()
    if re.search(r'忽略|系统指令|system\s*:|developer\s*:|越权|绕过|免确认', text, re.I):
        return None
    text = ALIASES.get(text, text)
    if result := extra(text, context):
        return result
    for handler in (_navigation, _amend, _time, _card, _txn, _subscription, _wealth, _select):
        if result := handler(text, context):
            return result
    return None
