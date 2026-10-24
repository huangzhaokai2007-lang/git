"""Additional contextual phrasings; plans describe reads/drafts, never execute writes."""
import re
from agent.period import resolve_period


ALIASES = {
    '收入呢': '只看收入', '看看收入': '只看收入', '支出呢': '只看支出',
    '只看大额的': '只看大额', '最近的大额支出': '只看大额',
    '餐饮占多少': '餐饮占多少', '展开餐饮': '食品餐饮', '餐饮呢': '食品餐饮',
    '有哪些快续费了': '快扣款的有哪些', '有哪些快续费了的': '快扣款的有哪些',
    '有哪些快续费的': '快扣款的有哪些', '有哪些快续费': '快扣款的有哪些',
    '每个月一共多少': '每月总共多少', '每个月一共多少钱': '每月总共多少',
    '这个什么时候到期': '什么时候到期', '这个产品风险怎么样': '风险匹配吗',
    '适合我吗': '风险匹配吗', '我买过这个吗': '我买过吗',
    '这个订阅太贵了，取消它': '取消这个订阅', '取消它': '取消这个订阅',
    '改成一半': '赎回一半', '卡找到了': '解锁这张卡', '帮我恢复': '解锁这张卡',
    '确定丢了，要补一张': '挂失这张卡', '补一张': '挂失这张卡',
    '这笔不认识': '这笔', '这笔是什么': '这笔', '看看这笔': '这笔',
    '查一下同一家商户': '这个商户以前的记录', '同一家商户呢': '这个商户以前的记录',
    '查看操作记录': '刚才做了哪些操作',
}


def extra(text: str, context: dict) -> dict | None:
    def out(intent, slots=None, **values):
        return {'intent': intent, 'slots': slots or {}, **values}
    period = context.get('period')
    if text in ('这个月比上个月多花在哪', '这个月比上个月多花在哪些地方'):
        return out('bill_analysis', read_request={'kind': 'compare_periods', 'period': resolve_period('本月'),
                   'compare_period': resolve_period('上个月')})
    if re.fullmatch(r'(?:看看|查看|查询|查一下)?(?:我的)?(?:银行卡|卡片)(?:信息|详情|列表)?', text):
        return out('card_query')
    if text in ('其他卡呢', '看看其他卡', '全部卡呢', '哪张卡花得最多', '主要是哪张卡'):
        return out('card_query', read_request={'kind': 'card_bills', 'period': period, 'all_cards': True,
                                             'rank': '最多' in text}) if period else out('card_query')
    if text in ('最近有没有消费', '最近有没有交易', '最近的交易', '再看看最近的交易'):
        return out('txn_query', read_request={'kind': 'transactions', 'period': period}) if period else out(
            'txn_query', ask='请指定要查询的月份或日期范围。')
    if text in ('主要花在哪', '主要花在哪里', '主要花在哪些商户'):
        return out('txn_query', read_request={'kind': 'merchant_breakdown', 'period': period})
    if text in ('这个月有没有重复扣款', '有没有重复扣款', '看看重复扣款'):
        return out('txn_query', read_request={'kind': 'txn_duplicates', 'period': period})
    if text in ('看看退款', '退款记录', '原来的消费是哪笔'):
        return out('txn_query', read_request={'kind': 'txn_refund', 'period': period})
    if text in ('哪些是自动扣费', '哪些是订阅扣费'):
        return out('txn_query', read_request={'kind': 'transactions', 'period': period, 'category': '订阅'})
    if text in ('刚刚办理成功了吗', '刚才成功了吗', '刚刚办理成功了没', '到账了吗', '这笔转账到账了吗',
                '刚才那笔转账到账了吗', '为什么没成功', '这笔为什么失败', '刚才做了哪些操作'):
        return out(context.get('intent') or 'smalltalk', action='status')
    if text in ('继续刚才的补卡', '我补卡到哪一步了', '在哪里领', '补卡进度'):
        return out('card_report_lost', action='loss_records')
    if text in ('订阅怎么办', '订阅会取消吗') and context.get('card_id'):
        return out('subscription_list', ask='锁卡不等于取消订阅，已有订阅不会自动取消。当前数据未记录订阅绑定的付款卡，可查询全部订阅后逐项核对。')
    if text in ('之前扣的钱呢', '取消订阅能退款吗'):
        return out('subscription_list', ask='取消订阅只影响后续扣费，不等于退款；当前演示不受理真实退款，可继续查询已有扣费流水。')
    if text in ('换张卡呢', '换一张卡', '用其他卡付款'):
        return out('card_query', ask='当前转账工具未提供银行卡付款渠道选择，不能直接替换付款卡。可以查询其他卡片状态和限额。')
    if text in ('照上次那笔再转', '照上次再转', '再给他转一次'):
        last = context.get('last_write', {})
        if last.get('intent') in ('transfer_single', 'transfer_scheduled'):
            return out(last['intent'], dict(last.get('slots', {})), action='new_draft')
        return out('transfer_single', ask='请明确收款人和金额，我会生成新的转账确认。')
    return None
