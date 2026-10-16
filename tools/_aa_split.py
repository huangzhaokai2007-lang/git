"""AA 收款的**共享事实包构造**（T9 与编排层确认卡共用同一份，避免两份口径漂移）。

**为什么单独一个模块**：
1. `tools/transfer.py` 已 **298 行**（距 `CLAUDE.md` 的 300 行红线只剩 2 行），装不下新函数；
2. F15 的确认卡必须显示「每人多少、余数多少」，而 T9 **没有 `preview_*`** —— 若编排层自己再算一遍，
   就出现两份分摊实现，改一处必漏另一处（这正是团队最忌讳的「口径漂移」）。

于是把「分摊 + 事实包」收敛到本模块的**唯一实现**：

- `tools/transfer.create_aa_request`（执行回执）用它；
- `agent/_write_aa._prepare_aa`（确认卡，**只算不执行**）也用它。

**本模块不是白名单工具**：规格 §2 的 T1–T18 契约与计数**不变**（有 `tests/test_spec_counts.py` 守着），
它只供工具层与编排层内部复用。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from tools._query_common import _money_facts


def split_amount(amount: int, count: int) -> tuple[int, int]:
    """整数分均摊：返回 `(per_person, initiator_share)` —— **余数给发起人**。

    保证 `per_person * count + initiator_share == amount` 精确相等（纯整数运算，禁浮点）。
    """
    per_person = amount // count
    return per_person, amount - per_person * count


def aa_facts(payees: Sequence[Mapping], amount: int, *, extra: Mapping | None = None) -> dict:
    """AA 的事实包：人数 / 总额 / 每人金额 / 余数 / 档位 / 收款人名单。

    金额一律**整数分本体 + `*_yuan` 展示串**成对给出（供数字校验器比对「元」写法）。
    `extra` 供执行侧补 `request_id` 这类只在真发起后才存在的键。
    """
    per_person, share = split_amount(amount, len(payees))
    facts: dict = {
        "payee_count": len(payees),
        "tier": "L1",                       # 来源：规格 §2 T9 权限档列 = L1
        **_money_facts(amount, "amount"),
        **_money_facts(per_person, "per_person_amount"),
        **_money_facts(share, "initiator_share"),
        "total_check": per_person * len(payees) + share,
        "payees": [{"id": payee["id"], "name": payee["name"]} for payee in payees],
    }
    if extra:
        facts.update(extra)
    return facts
