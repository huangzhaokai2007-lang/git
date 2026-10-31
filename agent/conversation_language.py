"""Narrow, deterministic short answers; business validation remains in existing flows."""
import re

from agent import classifier, pending_slots, scene_language
from agent.period import resolve_day


def understand(text: str, session: str) -> classifier.IntentOut | None:
    raw = str(text or '').strip().rstrip('。？！?!').strip()
    item = pending_slots.peek(session)
    intent, slots = '', {}
    if item and item['intent'] in ('anomaly_check', 'bill_report', 'bill_analysis'):
        decision = scene_language.plan(raw, {'intent': item['intent']}) or {}
        period = decision.get('slots', {}).get('period')
        if period:
            intent, slots = item['intent'], {**item['slots'], 'period': period}
    if item and item['intent'] == 'transfer_single':
        amount = re.fullmatch(r'(?:金额(?:是|为)?|转)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块)?', raw)
        payee = re.fullmatch(r'(?:收款人(?:是|为)|给)\s*([^，,。！？!?\s]{1,20})', raw)
        if amount and 'amount' in item['missing']:
            intent, slots = item['intent'], {**item['slots'], 'amount': amount[1]}
        elif payee and 'payee' in item['missing']:
            intent, slots = item['intent'], {**item['slots'], 'payee': payee[1]}
    if item and item['intent'] == 'txn_query':
        start, end = resolve_day(raw, edge='start'), resolve_day(raw, edge='end')
        if start and end:
            dates = {key: value for key, value in (('date_from', start), ('date_to', end))
                     if key in item['missing']}
            intent, slots = item['intent'], {**item['slots'], **dates}
    return classifier.IntentOut(intent=intent, confidence=1, slots=slots) if intent else None


def colloquial(text: str) -> classifier.IntentOut | None:
    """Only handle a whole, unambiguous utterance after contextual reference resolution."""
    raw = str(text or '').strip().rstrip('。？！?!').strip()
    intent, slots = '', {}
    transfer = re.fullmatch(r'(?:麻烦|帮我)?(?:给|向)([^，,。！？!?\s]{1,20}?)\s*(?:转账|转|打)\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块)(?:吧|呗)?', raw)
    informal = re.search(r'^(?:麻烦|帮我)|打\s*\d|块|呗$', raw)
    if transfer and informal and transfer[1] not in ('他', '她', '它', '这位', '这个人', '那个人', '对方') and not transfer[1].startswith('第'):
        intent, slots = 'transfer_single', {'payee': transfer[1], 'amount': transfer[2]}
    if not intent and raw in ('帮我瞅瞅还剩多少钱', '我卡里还有多少啊', '查下余额呗',
                               '瞅瞅余额', '还有多少余额', '查下余额', '余额还有多少'):
        intent = 'balance_query'
    if not intent:
        return None
    return classifier.IntentOut(intent=intent, confidence=1, slots=slots)
