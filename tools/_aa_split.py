"""AA 收款的**共享事实包构造** + T31（按人金额版发起）—— 工具层纯实现，无 LLM。

**为什么单独一个模块**：
1. `tools/transfer.py` 已 300 行顶格，装不下新函数；
2. F15 的确认卡必须显示「每人多少、余数多少」，而 T9 **没有 `preview_*`** —— 若编排层自己再算一遍，
   就出现两份分摊实现，改一处必漏另一处（这正是团队最忌讳的「口径漂移」）。

于是把「分摊 + 事实包」收敛到本模块的**唯一实现**：

- `tools/transfer.create_aa_request`（T9 一键均摊执行）与 `agent/_write_aa._prepare_aa`（确认卡，只算不执行）共用 `aa_facts`；
- T31 `create_aa_request_with_amounts`（**按人金额**发起）是 F15 追加需求：
  用户说「王五 100 元、赵六 200 元」时执行侧要走它（T9 签名冻结，不能加参数 —— 新工具不破契约）。

**本模块的工具函数计入规格 §2**（T31）；`split_amount`/`aa_facts` 是内部共享件，不计。
"""

from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from data import dao

from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _invalid, _ok, _money_facts, current_session_id,
    current_user_id, require_owned,
)
from tools.schemas import AaAmountsReq, ErrorCode, ToolResult


def split_amount(amount: int, count: int) -> tuple[int, int]:
    """整数分均摊：返回 `(per_person, initiator_share)` —— **余数给发起人**。

    保证 `per_person * count + initiator_share == amount` 精确相等（纯整数运算，禁浮点）。
    """
    per_person = amount // count
    return per_person, amount - per_person * count


def aa_facts(payees: Sequence[Mapping], amount: int, *, extra: Mapping | None = None,
             per_person: Sequence[int] | None = None) -> dict:
    """AA 的事实包：人数 / 总额 / 每人金额 / 余数 / 档位 / 收款人名单。

    金额一律**整数分本体 + `*_yuan` 展示串**成对给出（供数字校验器比对「元」写法）。
    `per_person`（可选，与 payees 等长的整数分）：**按人金额**模式 —— 事实包给逐人明细、
    `split_mode="custom"`；缺省 = **一键均摊**（`split_mode="equal"`，余数给发起人）。
    `extra` 供执行侧补 `request_id` 这类只在真发起后才存在的键。
    """
    if per_person:
        per_person = [int(cents) for cents in per_person]
        lines = [{"id": payee["id"], "name": payee["name"], **_money_facts(cents, "amount")}
                 for payee, cents in zip(payees, per_person)]
        facts: dict = {
            "payee_count": len(payees),
            "tier": "L1",                       # 来源：规格 §2 T9/T31 权限档列 = L1（收款方向免码）
            **_money_facts(amount, "amount"),
            "split_mode": "custom",
            "per_person_lines": lines,
            "total_check": sum(per_person),
            "payees": [{"id": payee["id"], "name": payee["name"]} for payee in payees],
        }
    else:
        per_person_amount, share = split_amount(amount, len(payees))
        facts = {
            "payee_count": len(payees),
            "tier": "L1",                       # 来源：规格 §2 T9 权限档列 = L1
            **_money_facts(amount, "amount"),
            **_money_facts(per_person_amount, "per_person_amount"),
            **_money_facts(share, "initiator_share"),
            "split_mode": "equal",
            "total_check": per_person_amount * len(payees) + share,
            "payees": [{"id": payee["id"], "name": payee["name"]} for payee in payees],
        }
    if extra:
        facts.update(extra)
    return facts


# ---------------- T31 create_aa_request_with_amounts（SPEC-CHANGE 新增 · L1） ----------------
#
# 档位口径：AA 是**收款**（钱进来不是出去）→ L1、无 OTP（与 T9 同档）；
# 黑名单成员的拦截在编排层解析关卡（与 T9 同一套）。

def create_aa_request_with_amounts(payee_ids: list[str], amounts: list[int]) -> ToolResult:
    """T31 AA 收款请求（**按人金额版** · L1）。data: `request_id` / `total_amount`。

    与 T9 的区别：每人金额由调用方给定（`amounts` 与 `payee_ids` 等长、全为正整数分），
    T9 只有一键均摊。同样**不落资金**（无 AA 表），只写一条审计；收款人必须属于当前用户。
    """
    if (bad := _invalid(AaAmountsReq, payee_ids=payee_ids, amounts=amounts)) is not None:
        return bad
    if len(amounts) != len(payee_ids):
        return _fail(ErrorCode.INVALID_ARGUMENT, "每人金额的个数必须与收款人一致")
    if any(not pid.strip() for pid in payee_ids):
        return _fail(ErrorCode.INVALID_ARGUMENT, "收款人不能为空")
    if len(set(payee_ids)) != len(payee_ids):
        return _fail(ErrorCode.INVALID_ARGUMENT, "收款人不能重复")
    try:
        require_owned("这些收款人", DEMO_USER_ID, current_user_id(),
                      tool="create_aa_request_with_amounts", intent="aa_collect")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    payees = []
    for payee_id, cents in zip(payee_ids, amounts):
        row = dao.get_payee(payee_id)
        if row is None or row["user_id"] != current_user_id():
            return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
        payees.append({"id": row["id"], "name": row["name"], "amount": int(cents)})
    total = sum(int(cents) for cents in amounts)
    request_id = f"aa_{uuid.uuid4().hex[:12]}"
    facts = aa_facts(payees, total, per_person=[p["amount"] for p in payees],
                     extra={"request_id": request_id})
    facts["target_name"] = "、".join(p["name"] for p in payees)
    dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                     intent="aa_collect", tool="create_aa_request_with_amounts",
                     params_json={"payee_ids": payee_ids, "amounts": [int(c) for c in amounts]},
                     risk_level="L1", permission_tier="L1", result="success", error_code=None)
    return _ok({"request_id": request_id, "total_amount": total}, facts,
               f"已发起 AA 收款 {total / 100:.2f} 元（{len(payees)} 人，按人金额）")
