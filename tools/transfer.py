"""工具层 T6–T9（任务卡 05；卡 05b 拆分后）：收款人解析 + 转账三段式（preview → 权限档 → 确认 → 幂等执行）。

依据 `docs/01-接口规格.md` 第 2 节 T6–T9、第 4 节状态机、第 5 节权限矩阵 + `CLAUDE.md` 铁律：
- **只算不执行**：`preview_transfer` 不写任何流水；只有 `execute_transfer` 在**一个事务**里
  扣款 + 写流水 + 写审计 + 状态翻转（复用 DAO `_writing()` 的 `in_transaction` 检测，绝不嵌套 `BEGIN` —— 台账 R1）。
- 金额一律整数分（无浮点）；**OTP 明文绝不进日志、facts 或回执**；`execute` 里不调用任何 LLM。
- 幂等：`preview_token` 是**一次性**的；执行后把结果快照写回 token，同 token 重复调用返回同一结果、
  绝不重复扣款。**并发**：token 状态检查 + 翻转 + 扣款事务同处 `_TOKENS_LOCK` 临界区（卡 04b 修复
  线程级 TOCTOU）；`state` 翻转在事务内完成，事务回滚时状态一并回退。
- 归属：收款人 / 账户必须属于当前用户（越权 → `FORBIDDEN`），会话用户沿用 `tools._query_common.set_current_user`。

模块分工（卡 05b 拆分后，依赖单向 `transfer → {_transfer_risk, _query_common, schemas}`）：
- §5 权限档 / 降级因子 / 剩余限额 → `tools/_transfer_risk.py`（时间由参数注入）；
- 薄封装与会话上下文 → `tools/_query_common.py`；出入参模型 → `tools/schemas.py`；
- 本模块只留 T6–T9 公开函数 + token 表/锁 + `_now`（测试用假时钟钉的就是它）。

口径与已知缺口（逐条写在交付说明的「需要人类决定」里）：
- `fee` 恒为 **0**：规格 §5 T7 备注已定「无费率口径，demo 期恒 0」，未自行编造费率。
- `new_payee` 不再参与升档（规格 §5 已去重：它已是 L2 基础条件），`device_change` / `geo_change` 无数据源 → 不实现。
- L2 的「金额 > 500元」分支被硬约束「单笔上限 5 万分（500元）」遮蔽：>500元 直接 `OVER_LIMIT`。
- 扣款账户 = 当前用户**储蓄账户**（T7 无账户入参）。
- L3 只拒绝自动执行（`INVALID_STATE`）：60s 延迟生效/可撤销窗口属编排层 `PENDING_REVIEW`（卡 10）。
- DAO 原语已补齐：`dao.get_payee(payee_id)` / `dao.update_account_balance(account_id, delta)`。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta

from data import dao
from data.db import transaction
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money, _money_facts, _ok, _owned_account_ids, current_user_id,
    require_owned, set_current_user, set_session_id, current_session_id,
)
from tools._transfer_risk import (
    L1_MAX_CENTS, SINGLE_TX_MAX_CENTS, DAILY_MAX_CENTS, NIGHT_START_HOUR, VELOCITY_MINUTES_WRITE,
    VELOCITY_MIN_WRITES, AMOUNT_JUMP_RATIO, HISTORY_DAYS, TIER_ORDER, OTP_TIERS, _today_out_flows,
    _history_mean_cents, _factors, _assess, _limits, NIGHT_END_HOUR,
)
from tools.schemas import (
    ResolvePayeeReq, PayeeCandidate, PayeeData, PreviewTransferReq, PreviewData, ExecuteTransferReq,
    ExecuteData, AaRequestReq, AaData, ErrorCode, ToolResult,
)

from guard import tool_guard
logger = logging.getLogger(__name__)

# ---------------- 本模块阈值常量 ----------------

PREVIEW_TTL_SECONDS = 300           # 来源：规格 §2 T7「preview_token 有 TTL 300s 且绑定参数」

OTP_CODE = "123456"                 # 来源：规格 §5 L2「demo 中固定 123456」；**绝不写日志/facts/回执**


_TOKENS: dict[str, dict] = {}
#: 幂等临界区（卡 04b）：token 状态检查 → 翻转 → 扣款事务必须整体串行。
#: 旧实现把 `state == "executed"` 检查放在事务外，两线程可同时通过 → 双重扣款（线程级 TOCTOU）。
#: 另注：DAO 是进程内**单个** SQLite 连接，并发写本来也必须串行，一把锁同时解决这两个问题。
_TOKENS_LOCK = threading.Lock()


def _now() -> datetime:
    """取当前时间（单独抽出来是为了让测试能钉住「凌晨/短时高频」这类时间相关口径）。"""
    return datetime.now()


def resolve_payee(query: str) -> ToolResult:
    """T6 收款人解析（L0）。data: `candidates[{id,name,masked_phone}]` / `ambiguous`。

    同名多个 → `ambiguous=True` 并**全部**列出候选，绝不擅自选一个；匹配口径 = DAO 的
    姓名/手机号/银行子串匹配（规格 T6 入参原写「备注」，但 `payee` 表无备注列 —— 已按
    SPEC-CHANGE 删「备注」，T6 只按 name/phone 匹配）。
    只返回当前用户的收款人；无命中 → `ok=True` + 空候选（由编排层反问），不报 NOT_FOUND。
    """
    if (bad := _invalid(ResolvePayeeReq, query=query)) is not None:
        return bad
    try:
        rows = [row for row in dao.find_payee(query) if row["user_id"] == current_user_id()]
    except ValueError as exc:
        return _dao_reject(exc)
    candidates = [{"id": row["id"], "name": row["name"], "masked_phone": row["phone"]} for row in rows]
    data = PayeeData(candidates=[PayeeCandidate(**item) for item in candidates],
                     ambiguous=len(candidates) > 1)
    facts = {"candidate_count": len(candidates), "ambiguous": data.ambiguous, "query": query,
             "candidates": [{"id": item["id"], "name": item["name"], "phone": item["masked_phone"]}
                            for item in candidates]}
    if not candidates:
        return _ok(data.model_dump(), facts, "没有找到匹配的收款人，请补充姓名或手机号。")
    if data.ambiguous:
        return _ok(data.model_dump(), facts,
                   f"找到 {facts['candidate_count']} 个同名或相近的收款人，请确认是哪一个。")
    only = facts["candidates"][0]
    return _ok(data.model_dump(), facts, f"找到收款人 {only['name']}（{only['phone']}）。")

def preview_transfer(payee_id: str, amount: int, schedule: str | None = None,
                     split_with: list[str] | None = None) -> ToolResult:
    """T7 转账预览（L0，**只算不执行**）。data: `preview_token` / `fee` / `tier` / `requires_otp` / `limits`。

    token 存内存、TTL 300 秒、绑定（payee_id + amount + 用户 + 档位）；本函数**不写任何流水与审计**。
    `schedule` / `split_with` 的语义属后续卡（定时/拆分转账），本卡只拒不做：传了就 `INVALID_ARGUMENT`。
    """
    if (bad := _invalid(PreviewTransferReq, payee_id=payee_id, amount=amount,
                        schedule=schedule, split_with=split_with)) is not None:
        return bad
    try:                                                              # 卡 14a：统一金额边界
        tool_guard.require_amount_cents(amount, tool="preview_transfer")
        tool_guard.require_payee_exists(payee_id, tool="preview_transfer")   # 卡 14b-4：按 id 精确校验
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    if schedule is not None or split_with is not None:
        logger.warning("本卡未实现定时/拆分转账：schedule=%r split_with=%r", schedule, split_with)
        return _fail(ErrorCode.INVALID_ARGUMENT, "本卡还不支持定时转账或拆分转账")
    try:
        payee = dao.get_payee(payee_id)
        if payee is None:
            return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
        require_owned("收款人", payee["user_id"], payee["id"], tool="preview_transfer")
        account = dao.get_balance("savings")
        if account is None or account["user_id"] != current_user_id():
            return _fail(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
        flows = _today_out_flows(_now())
        limits = _limits(flows)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)

    fee = 0                                                     # 规格未定义手续费口径（见文件头）
    if amount > SINGLE_TX_MAX_CENTS:
        return _fail(ErrorCode.OVER_LIMIT, "超过单笔转账上限")
    if limits["daily_used"] + amount > DAILY_MAX_CENTS:
        return _fail(ErrorCode.OVER_LIMIT, "超过单日累计转账上限")
    if account["balance"] < amount + fee:
        return _fail(ErrorCode.INSUFFICIENT_FUNDS, "储蓄账户余额不足")

    judged = _assess(payee, amount, flows, _now())
    mean, sample = _history_mean_cents(_now())
    token = f"pt_{uuid.uuid4().hex}"
    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    limits_yuan = {f"limits_{key}_yuan": _money(value) for key, value in limits.items()}
    facts = {"payee_id": payee["id"], "payee_name": payee["name"], "tier": judged["tier"],
             "requires_otp": judged["requires_otp"], "factors": judged["factors"],
             "factor_count": len(judged["factors"]), "escalation": judged["escalation"],
             "to_human": judged["to_human"],
             "ttl_seconds": PREVIEW_TTL_SECONDS, "token_bound_to": "payee_id+amount+tier",
             "amount_ratio_threshold": AMOUNT_JUMP_RATIO, "history_days": HISTORY_DAYS,
             "history_sample": sample, "night_from_hour": NIGHT_START_HOUR, "night_to_hour": NIGHT_END_HOUR,
             "velocity_window_minutes": VELOCITY_MINUTES_WRITE, "velocity_min_writes": VELOCITY_MIN_WRITES,
             **{f"limits_{key}": value for key, value in limits.items()}, **limits_yuan,
             **_money_facts(account["balance"], "balance"), **_money_facts(fee, "fee"),
             **_money_facts(amount, "amount"), **_money_facts(mean, "history_mean")}
    data = PreviewData(preview_token=token, fee=fee, tier=judged["tier"],
                       requires_otp=judged["requires_otp"], limits=limits)
    _TOKENS[token] = {"user_id": current_user_id(), "session_id": current_session_id(),
                      "payee_id": payee["id"], "payee_name": payee["name"], "amount": amount,
                      "fee": fee, "tier": judged["tier"], "requires_otp": judged["requires_otp"],
                      "factors": judged["factors"], "account_id": account["id"], "trace_id": trace_id,
                      "created_at": _now(), "state": "preview"}
    message = (f"转账 {facts['amount_yuan']} 元给 {payee['name']}：档位 {judged['tier']}，"
               f"手续费 {facts['fee_yuan']} 元，"
               f"{'需要' if judged['requires_otp'] else '不需要'} OTP；"
               f"单笔上限 {limits_yuan['limits_single_max_yuan']} 元，"
               f"今日剩余 {limits_yuan['limits_daily_remaining_yuan']} 元。")
    return _ok(data.model_dump(), facts, message)

def execute_transfer(preview_token: str, otp: str | None = None) -> ToolResult:
    """T8 转账执行（L1–L3）。data: `txn_id` / `amount` / `payee_name` / `balance_after`。

    顺序：token 存在 → 归属 → 未过期 → OTP（L2/L3）→ 已执行则返回快照（**幂等**）→ L3 拒绝自动执行
    → 事务内（扣款 + 写流水 + 写审计 + 状态翻转）→ 返回快照。本函数不调用任何 LLM。

    **并发**：整段执行在 `_TOKENS_LOCK` 临界区内 —— 两线程同时执行同一 token 时，后到者必然看到
    已翻转的状态并走幂等分支，绝不会第二次扣款（卡 04b 修复线程级 TOCTOU）。
    """
    if (bad := _invalid(ExecuteTransferReq, preview_token=preview_token, otp=otp)) is not None:
        return bad
    with _TOKENS_LOCK:                                          # 临界区：检查 → 扣款 → 翻转，整体串行
        return _execute_locked(preview_token, otp)

def _execute_locked(preview_token: str, otp: str | None) -> ToolResult:
    """`execute_transfer` 的临界区主体；调用方必须已持有 `_TOKENS_LOCK`。"""
    token = _TOKENS.get(preview_token)
    if token is None:
        return _fail(ErrorCode.TOKEN_EXPIRED, "预览不存在或已失效，请重新预览")
    if token["user_id"] != current_user_id():
        return _fail(ErrorCode.FORBIDDEN, "该预览不属于当前用户")
    if _now() - token["created_at"] > timedelta(seconds=PREVIEW_TTL_SECONDS):
        _TOKENS.pop(preview_token, None)
        return _fail(ErrorCode.TOKEN_EXPIRED, "预览已过期，请重新预览")
    if token["requires_otp"] and otp != OTP_CODE:
        logger.warning("OTP 校验失败：token=%s user=%s", preview_token[:8], token["user_id"])
        return _fail(ErrorCode.FORBIDDEN, "验证码不正确")
    if token["state"] == "executed":                            # 幂等：同 token 返回同一结果
        return _ok(token["data"], token["facts"], token["message"])
    if token["tier"] == "L3":
        return _fail(ErrorCode.INVALID_STATE, "该笔需人工复核，不能自动执行")

    amount, account_id = token["amount"], token["account_id"]
    try:                                                        # 预览之后余额/限额可能变化 → 执行前重校验
        account = dao.get_balance("savings")
        if account is None or account["id"] != account_id or account["user_id"] != current_user_id():
            return _fail(ErrorCode.FORBIDDEN, "储蓄账户不属于当前用户")
        limits = _limits(_today_out_flows(_now()))
        if amount > SINGLE_TX_MAX_CENTS or limits["daily_used"] + amount > DAILY_MAX_CENTS:
            return _fail(ErrorCode.OVER_LIMIT, "超过转账限额")
        if account["balance"] < amount + token["fee"]:
            return _fail(ErrorCode.INSUFFICIENT_FUNDS, "储蓄账户余额不足")
        conn = dao.connection()
        with transaction(conn):                                 # 单一事务：扣款 + 流水 + 审计 + 状态翻转（不嵌套 BEGIN）
            updated = dao.update_account_balance(account_id, -amount)
            if updated is None:                                 # 防御性兜底，正常走不到
                raise ToolError(ErrorCode.NOT_FOUND, "储蓄账户不存在")
            balance_after = updated["balance"]
            txn = dao.insert_txn(account_id, _now().isoformat(timespec="seconds"), -amount, "out",
                                 balance_after, counterparty=token["payee_name"], category="转账",
                                 channel="转账", memo=f"转账给{token['payee_name']}")
            dao.insert_audit(token["trace_id"], token["session_id"], actor="agent",
                             intent="transfer_single", tool="execute_transfer",
                             params_json={"payee_id": token["payee_id"], "amount": amount,
                                          "tier": token["tier"], "factors": token.get("factors", [])},
                             risk_level=token["tier"], permission_tier=token["tier"], result="success")
            facts = {"txn_id": txn["id"], "payee_id": token["payee_id"], "payee_name": token["payee_name"],
                     "tier": token["tier"], "requires_otp": token["requires_otp"], "status": "executed",
                     **_money_facts(amount, "amount"), **_money_facts(token["fee"], "fee"),
                     **_money_facts(balance_after, "balance_after")}
            data = ExecuteData(txn_id=txn["id"], amount=amount, payee_name=token["payee_name"],
                               balance_after=balance_after)
            message = (f"已向 {data.payee_name} 转账 {facts['amount_yuan']} 元，"
                       f"账户余额 {facts['balance_after_yuan']} 元。")
            # 状态翻转与扣款**同事务**：失败时由 _rollback_token 一起回退，
            # 绝不出现「状态说 executed、库里却没有这笔流水」的假成功。
            token.update({"state": "executed", "data": data.model_dump(), "facts": facts, "message": message})
    except ToolError as exc:
        _rollback_token(token)
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        _rollback_token(token)
        return _dao_reject(exc)
    return _ok(token["data"], token["facts"], token["message"])

def _rollback_token(token: dict) -> None:
    """事务失败 → token 状态与快照一并回退，保证 executed 状态必然对应已提交的流水。"""
    token.update({"state": "preview", "data": None, "facts": None, "message": None})

def create_aa_request(payee_ids: list[str], amount: int) -> ToolResult:
    """T9 AA 收款请求（L1）。data: `request_id` / `per_person_amount`。

    拆分：`per_person_amount = amount // 人数`（整数分均摊），**余数给发起人**（进 facts 的
    `initiator_share`），故 `per_person * 人数 + initiator_share == amount` 精确相等。
    不落资金（无 AA 表），只写一条审计；收款人必须属于当前用户。
    """
    if (bad := _invalid(AaRequestReq, payee_ids=payee_ids, amount=amount)) is not None:
        return bad
    if any(not pid.strip() for pid in payee_ids):
        return _fail(ErrorCode.INVALID_ARGUMENT, "收款人不能为空")
    if len(set(payee_ids)) != len(payee_ids):
        return _fail(ErrorCode.INVALID_ARGUMENT, "收款人不能重复")
    try:
        payees = []
        for payee_id in payee_ids:
            row = dao.get_payee(payee_id)
            if row is None:
                return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
            require_owned("收款人", row["user_id"], row["id"], tool="create_aa_request")
            payees.append(row)
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        request_id = f"aa_{uuid.uuid4().hex[:12]}"
        dao.insert_audit(trace_id, current_session_id(), actor="agent", intent="aa_collect",
                         tool="create_aa_request",
                         params_json={"payee_ids": [payee["id"] for payee in payees], "amount": amount},
                         risk_level="L1", permission_tier="L1", result="success")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    per_person = amount // len(payees)
    share = amount - per_person * len(payees)                   # 余数给发起人
    facts = {"request_id": request_id, "payee_count": len(payees), "tier": "L1",
             **_money_facts(amount, "amount"), **_money_facts(per_person, "per_person_amount"),
             **_money_facts(share, "initiator_share"), "total_check": per_person * len(payees) + share,
             "payees": [{"id": payee["id"], "name": payee["name"]} for payee in payees]}
    data = AaData(request_id=request_id, per_person_amount=per_person)
    message = (f"已发起 AA 收款 {facts['amount_yuan']} 元，{facts['payee_count']} 人各 "
               f"{facts['per_person_amount_yuan']} 元，余数 {facts['initiator_share_yuan']} 元由你承担。")
    return _ok(data.model_dump(), facts, message)
