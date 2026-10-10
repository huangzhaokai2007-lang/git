"""F06 单日限额收紧既有转账上限，保持冻结的转账输入/输出字段。"""
from data import card_limit, dao
from tools._query_common import current_user_id


def restrict(limits: dict, account_id: str) -> dict:
    cap = card_limit.daily_cap(dao.connection(), account_id, current_user_id())
    if cap is None:
        return limits
    maximum = min(limits["daily_max"], cap)
    return {**limits, "daily_max": maximum,
            "daily_remaining": max(maximum - limits["daily_used"], 0)}
