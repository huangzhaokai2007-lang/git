"""预约的两个**用户操作**写描述符族（T32 立即执行 / T33 改时间）—— F16 追加需求。

从 `agent/_write_transfer.py` 拆出（原文件 290 行，塞不下且不破 300 红线）；口径与「取消预约」
（T23 族）逐项同构：**都从只读 T24 取那一笔**、都不直查库。

| 意图 | 工具 | 档位 | 扣款 |
|---|---|---|---|
| `scheduled_run`（立即执行） | T32 `run_scheduled_now` | **沿用预约冻结的档位**（L1→免码 / L2→要码） | **扣** |
| `scheduled_modify`（改时间） | T33 `reschedule_scheduled` | **恒 L1**（只改时间、不扣款） | 不扣 |

**为什么立即执行沿用冻结档位**：F16 的 Q5 口径是「预约落库时定档、到期不再重定」——提前执行
同理；若现在重新定档，23:00 点的"立即执行"会被判 L3 转人工，等于让用户替系统背锅。
额度过限/余额不足仍由 T22 的硬约束重检（那份实现不重写）。
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card
from agent._write_common import Prepared, ShapeResult, _code_of
from agent._write_transfer import _iso_schedule
from tools import _transfer_schedule
from tools.schemas import ToolResult

#: 改时间恒 L1（只改未来某一笔的时间，不动钱）
MODIFY_TIER = "L1"
#: 多笔待执行时的追问（挑哪一笔）
PICK_AMBIGUOUS = "您有多笔尚未执行的预约，请说明是哪一笔（可以说出预约时间或收款人）。"
#: 缺新时间的追问
NEW_TIME_CLARIFY = "请说明新的预约时间，格式如 2026-11-01 09:00。"


def _pick(slots: Mapping, ambiguous: str) -> tuple[dict | None, str | None, list[str], str]:
    """挑一笔待执行预约：给了 `schedule_id`（界面按钮）就用它；否则一笔→直接定，多笔→追问。

    返回 `(行, 错误码, 缺槽, 追问文案)`；四项里只会有一项非空。
    """
    filled = dict(slots)
    items, code = _pending_listed()
    if code is not None:
        return None, code, [], "取不到您的预约列表"
    if filled.get("schedule_id"):
        row = next((it for it in items if it["schedule_id"] == filled["schedule_id"]), None)
        if row is None:
            return None, "NOT_FOUND", [], "这笔预约已不在待执行状态"
        return row, None, [], ""
    if not items:
        return None, "NOT_FOUND", [], "您没有待执行的预约"
    if len(items) > 1:
        return None, None, ["schedule_id"], ambiguous
    return items[0], None, [], ""


# ---------------- 立即执行（T32） ----------------

def _shape_run(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：挑一笔待执行预约（界面按钮会直接给 `schedule_id`，零歧义）。"""
    filled = dict(slots)
    row, code, missing, clarify = _pick(filled, PICK_AMBIGUOUS)
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message=clarify)
    if row is None:
        return ShapeResult(filled=filled, missing=missing, clarify=clarify)
    filled["schedule_id"] = row["schedule_id"]
    filled["amount_cents"] = row["amount"]
    return ShapeResult(filled=filled)


def _prepare_run(filled: dict) -> Prepared:
    """只读那一笔（不调 T32 —— 扣款在 EXECUTE）；档位**沿用预约冻结值**。"""
    items, code = _pending_listed()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的预约列表")
    row = next((it for it in items if it["schedule_id"] == filled.get("schedule_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="这笔预约已不在待执行状态")
    return Prepared(ok=True, facts=dict(row), credential="")


def _card_run(intent: str, filled: dict, facts: Mapping, tier: str,
              requires_otp: bool) -> confirm_card.CardInput:
    """立即执行确认卡：谁 / 多少 / 原预约时间 / **现在就会扣款**。"""
    return confirm_card.CardInput(
        title="立即执行预约确认卡", intent_cn="立即执行预约",
        rows=[("收款人", str(facts["payee_name"])),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("原预约时间", str(facts["execute_at"]).replace("T", " ")),
              ("说明", "现在就从您的账户扣款转账，不再等到预约时间")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "立即执行预约"))


def _plain_run(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    return (f"请确认：立即执行 {str(facts.get('execute_at')).replace('T', ' ')} 向 "
            f"{facts.get('payee_name')} 转账 {facts.get('amount_yuan')} 元的预约，"
            f"权限档 {tier}，会**立刻扣款**。")


def _execute_run(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T32**（结算复用 T22 的同一份实现）。"""
    del otp
    from tools import _transfer_schedule_ops
    return _transfer_schedule_ops.run_scheduled_now(str(confirmation.filled["schedule_id"]))


# ---------------- 改时间（T33） ----------------

def _shape_modify(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：挑一笔 + **新时间**（缺哪个补哪个，两件事一起问）。"""
    filled = dict(slots)
    row, code, missing, clarify = _pick(filled, PICK_AMBIGUOUS)
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message=clarify)
    if row is not None:
        filled["schedule_id"] = row["schedule_id"]
        filled["amount_cents"] = row["amount"]
    when = _iso_schedule(filled.get("schedule")) if filled.get("schedule") else None
    if when is None:
        missing = [*missing, "schedule"]
        clarify = clarify or NEW_TIME_CLARIFY
    else:
        filled["execute_at_new"] = when
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _prepare_modify(filled: dict) -> Prepared:
    """只读那一笔（不调 T33）：事实包带上原时间与新时间，供卡面对比。"""
    items, code = _pending_listed()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的预约列表")
    row = next((it for it in items if it["schedule_id"] == filled.get("schedule_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="这笔预约已不在待执行状态")
    return Prepared(ok=True, facts={**row, "tier": MODIFY_TIER,
                                    "execute_at_old": row["execute_at"],
                                    "execute_at_new": filled.get("execute_at_new")},
                    credential="")


def _card_modify(intent: str, filled: dict, facts: Mapping, tier: str,
                 requires_otp: bool) -> confirm_card.CardInput:
    """改时间确认卡：谁 / 多少 / **原时间 → 新时间**（只改时间，金额一分不动）。"""
    return confirm_card.CardInput(
        title="修改预约时间确认卡", intent_cn="修改预约时间",
        rows=[("收款人", str(facts["payee_name"])),
              ("金额", f"{facts['amount_yuan']} 元（不变）"),
              ("原预约时间", str(facts["execute_at_old"]).replace("T", " ")),
              ("新预约时间", str(facts["execute_at_new"]).replace("T", " "))],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "修改预约时间"))


def _plain_modify(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    return (f"请确认：把向 {facts.get('payee_name')} 转账 {facts.get('amount_yuan')} 元的预约"
            f"从 {str(facts.get('execute_at_old')).replace('T', ' ')} 改到 "
            f"{str(facts.get('execute_at_new')).replace('T', ' ')}，权限档 {tier}，不会扣款。")


def _execute_modify(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T33** 改时间（只改 `pending` 那一笔的时间）。"""
    del otp
    from tools import _transfer_schedule_ops
    return _transfer_schedule_ops.reschedule_scheduled(
        str(confirmation.filled["schedule_id"]), str(confirmation.filled["execute_at_new"]))


# ---------------- 取消预约（F16 · Q4：L1 确认卡、**不要 OTP**） ----------------

#: 取消的档位：**恒 L1**（Q4）。取消是「撤回一个尚未发生的动作」，风险低于发起，且不动钱。
#: **不沿用原预约冻结的档位** —— 否则一笔 L2 预约的取消也要 OTP，与 Q4 冲突。
CANCEL_TIER = "L1"

#: 有多笔待执行预约时的追问（纯措辞，不含业务数字）
CANCEL_AMBIGUOUS = "您有多笔尚未执行的预约，请说明要取消哪一笔（可以说出预约时间）。"


def _pending_listed() -> tuple[list[dict], str | None]:
    """只读 T24 取「待执行」预约；返回 (items, 错误码)。延迟 import 同 `_book_scheduled` 的理由。"""
    from tools import _transfer_schedule
    listed = _transfer_schedule.list_scheduled("pending")
    if not listed.ok:
        return [], _code_of(listed)
    return list(listed.facts.get("items") or []), None


def _shape_cancel(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：从**只读**列表里挑要取消的那一笔。

    给了 `schedule_id`（界面卡面按钮）→ 直接用它（零歧义）；否则：一笔 → 直接定；
    **零笔 → `NOT_FOUND`**；**多笔 → 追问**（绝不擅自挑一笔）。
    """
    filled = dict(slots)
    items, code = _pending_listed()
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message="取不到您的预约列表")
    if filled.get("schedule_id"):
        row = next((it for it in items if it["schedule_id"] == filled["schedule_id"]), None)
        if row is None:
            return ShapeResult(filled=filled, error_code="NOT_FOUND", message="这笔预约已不在待执行状态")
        filled["amount_cents"] = row["amount"]
        return ShapeResult(filled=filled)
    if not items:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="您没有待执行的预约")
    if len(items) > 1:
        return ShapeResult(filled=filled, missing=["schedule_id"], clarify=CANCEL_AMBIGUOUS)
    filled["schedule_id"] = items[0]["schedule_id"]
    filled["amount_cents"] = items[0]["amount"]                 # 供确认卡显示金额
    return ShapeResult(filled=filled)


def _prepare_cancel(filled: dict) -> Prepared:
    """取消的确认卡事实包：**只读**列表里那一笔（不调 T23 —— 落库在 EXECUTE 步）。

    `tier` 覆盖成 `CANCEL_TIER`：`write_flow` 用 `facts["tier"]` 定档，而这里的原值是被取消
    那笔的档位（可能是 L2/L3）。取消**恒 L1**（Q4），故必须覆盖。
    """
    items, code = _pending_listed()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的预约列表")
    row = next((it for it in items if it["schedule_id"] == filled.get("schedule_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="这笔预约已不在待执行状态")
    return Prepared(ok=True, facts={**row, "tier": CANCEL_TIER}, credential="")


def _card_cancel(intent: str, filled: dict, facts: Mapping, tier: str,
                 requires_otp: bool) -> confirm_card.CardInput:
    """取消预约确认卡：取消哪一笔（收款人 / 金额 / 预约时间）+ 「不会扣款」。"""
    return confirm_card.CardInput(
        title="取消预约确认卡", intent_cn="取消预约",
        rows=[("收款人", str(facts["payee_name"])),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("预约时间", str(facts["execute_at"]).replace("T", " ")),
              ("说明", "该预约尚未执行，取消后不会扣款")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "取消预约"))


def _plain_cancel(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（取消预约的降级回执）。"""
    return (f"请确认：取消 {facts.get('execute_at')} 向 {facts.get('payee_name')} 转账 "
            f"{facts.get('amount_yuan')} 元的预约，权限档 {tier}，不会产生扣款。")


def _execute_cancel(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T23** 取消（`pending → cancelled`，**不动钱**）。Q4：L1，无 OTP。"""
    del otp
    from tools import _transfer_schedule
    return _transfer_schedule.cancel_scheduled(str(confirmation.filled["schedule_id"]))
