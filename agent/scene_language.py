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
from agent.scene_context import read_scope

_BILLS = {'txn_query', 'bill_analysis', 'bill_report', 'anomaly_check'}
#: 规格 §3 / `classifier.SYSTEM_PROMPT` 的「账单总结」触发词：只有**明确要一份成品报告**的说法
#: 才判 `bill_report`；只是问「X月的账单」= 问花了多少 → `bill_analysis`。
#: 这张表必须与分类器提示词逐字一致 —— scene 层返回 confidence=1 会压过分类器，
#: 口径一旦漂移，同一句话在网页端和 CLI/API/IM/离线入口就会给出两个意图。
_REPORT_MARKS = ('总结', '汇总', '报告', '出一份', '给我一份')
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


#: 追问题开头的引导词（可以叠着说：「再看看 8 月」= 再 + 看看）
_LEAD = re.compile(r'^(?:那|那么|再|查一下|查询|查|看看|看一下|看)+')
_MONTH = r'(?:(\d{4})年)?([一二三四五六七八九十]+|\d{1,2})月'
_TAIL = r'(?:的)?(?:账单|消费|交易|支出)?(?:怎么样|呢|如何|比呢)?$'


def _month(text: str) -> str | None:
    """`{(\\d{4})年}?{月}` → `YYYY-MM`（缺年份时按锚点年补）。"""
    match = re.fullmatch(_MONTH, text)
    if not match:
        return None
    month = int(match[2]) if match[2].isdigit() else _NUMBERS.get(match[2], 0)
    return f'{match[1] or anchor_period()[:4]}-{month:02d}' if 1 <= month <= 12 else None


def _period(text: str) -> str | None:
    text = _LEAD.sub('', text)
    text = re.sub(_TAIL, '', text)
    text = re.sub(r'^和|^跟', '', text)
    return resolve_period(text) or _month(text)


def _embedded_period(text: str) -> str | None:
    """账期被意图词隔断时的兜底：「上个月的流水」「8月有什么异常支出」。

    `_period` 走的是"剥掉固定后缀 → 整串匹配"，于是账期后面只要多接了半个字（点名
    意图、加个「有什么」）就整条追问掉回分类器，上一轮的上下文全丢 —— 用户问流水、
    拿到的是上一轮的异常检测。

    只在**已经认出了显式意图词**时才兜底（见 `_time`），且账期永远是追问题里最先说
    出来的那个成分，所以从句首锚定搜一次即可：纯筛选类追问（「只看餐饮」）认不出
    意图词，不会走到这里，仍按老样子回分类器。
    """
    text = _LEAD.sub('', text)
    if match := re.match(_MONTH, text):
        month = int(match[2]) if match[2].isdigit() else _NUMBERS.get(match[2], 0)
        if 1 <= month <= 12:
            return f'{match[1] or anchor_period()[:4]}-{month:02d}'
    for word in ('上上个月', '上个月', '上月', '这个月', '本月', '当月'):
        if word in text:
            return resolve_period(word)
    return None


def _explicit_intent(text: str) -> str | None:
    """句子里出现**明确的意图词**时返回对应意图（覆盖继承）。

    2026-10-26 修：「上个月的流水」在上一轮是异常检测时被继承成 anomaly_check ——
    问流水答异常。词序沿用 `app/cli.py` OFFLINE_RULES 的口径：更具体的意图词优先
    （「异常支出」归异常、「账单总结」归报告，不能被泛词截走）。
    """
    if '异常' in text or '可疑' in text:
        return 'anomaly_check'
    if any(mark in text for mark in _REPORT_MARKS):
        return 'bill_report'
    if '流水' in text or '交易记录' in text:
        return 'txn_query'
    if '花了多少' in text or '消费' in text or '支出' in text:
        return 'bill_analysis'
    return None


def _time(text: str, context: dict) -> dict | None:
    span = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日至(\d{4})年(\d{1,2})月(\d{1,2})日的账单报告', text)
    if span:
        from datetime import date
        try:
            y, m, d, ey, em, ed = map(int, span.groups())
            period = f'{date(y, m, d).isoformat()} 至 {date(ey, em, ed).isoformat()}'
        except ValueError:
            return None
        return _out('bill_report', {'period': period, 'kind': 'range'})
    explicit = _explicit_intent(text)
    period = _period(text) or (explicit and _embedded_period(text))
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
    #: 意图归属（2026-10-26 修）：**显式意图词 > 前序继承 > bill_report**。
    #: 有前序账单意图就继承；没有前序时，「X月的账单」按开宗明义要账单处理 = bill_report
    #: （与 `tests/test_scene_integration.py` 锁定的口径一致，不动它）。
    #: 但句子里明确点名了意图（如「上个月的流水」）时，必须以前序为准让位 —— 继承只该
    #: 兜底省略了意图的追问（「那 5 月的呢」），不该改写用户点名的意图。
    intent = explicit or (previous if previous in _BILLS else 'bill_report')
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
    if _domain(context) == 'card' and text in ('这张卡的详情', '这张卡详情', '卡片详情', '看看详情'):
        card = _entity(text, context, 'card')
        return _out('card_query', read_request={'kind': 'card_detail', 'card_id': card}) if card else _out(
            'card_query', ask='请先选择具体银行卡。')
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
    if text in ('最大的一笔', '最大那笔', '最大一笔', '最大的一笔呢', '哪笔最大', '金额最大的一笔'):
        return _out('txn_query', read_request={'kind': 'txn_largest', **read_scope(context)})
    if text in ('用的是哪张卡', '这笔用的是哪张卡', '这笔是哪张卡', '关联哪张卡', '查看关联卡'):
        txn = context.get('txn_id')
        return _out('card_query', read_request={'kind': 'txn_cards', **read_scope(context), 'txn_id': txn}) if txn else _out(
            'txn_query', ask='请先选择具体交易，再查看其关联账户的银行卡。')
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
