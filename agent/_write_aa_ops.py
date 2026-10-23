"""AA 的两个**管理动作**写描述符族（T43 登记已付 / T44 撤销）—— F22 追加。

口径与「预约/订单操作」同构：都从只读 T42 取那一笔，都不直查库。

| 意图 | 工具 | 档位 | 说明 |
|---|---|---|---|
| `aa_mark_paid`（登记某人已付） | T43 `mark_aa_share_paid` | **L1** | 全部付清 → 请求自动转 `done`；重复登记 → `INVALID_STATE` |
| `aa_cancel`（撤销 AA） | T44 `cancel_aa_request` | **L1** | 已登记的付款记录保留（不回滚） |
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card, payee_resolve
from agent._write_common import Prepared, ShapeResult, _code_of
from tools.schemas import ToolResult

MARK_TIER, CANCEL_TIER = "L1", "L1"
PICK_AMBIGUOUS = "您有多笔进行中的 AA，请说明是哪一笔（可以说出事由或合计金额）。"
WHO_CLARIFY = "请说明是谁付了（例如「赵六付了」）。"


def _active() -> tuple[list[dict], str | None]:
    """只读 T42 取「进行中」的 AA 请求。"""
    from tools import _aa_request
    listed = _aa_request.list_aa_requests("active")
    if not listed.ok:
        return [], _code_of(listed)
    return list(listed.facts.get("items") or []), None


def _shape_mark_paid(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：哪一笔 AA + **谁付了**（缺哪个补哪个）。"""
    filled = dict(slots)
    missing: list[str] = []
    clarify = ""
    items, code = _active()
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message="取不到您的 AA 请求")
    if not items:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="您没有进行中的 AA 收款")
    if filled.get("request_id"):
        target = [it for it in items if it["request_id"] == filled["request_id"]]
        if not target:
            return ShapeResult(filled=filled, error_code="NOT_FOUND",
                               message="这笔 AA 已不在进行中")
        chosen = target[0]
    elif len(items) == 1:
        chosen = items[0]
    else:
        chosen = None
        missing.append("request_id")
        clarify = PICK_AMBIGUOUS
    person = None
    if filled.get("payee") and not filled.get("payee_id"):
        person, pcode, _facts = payee_resolve.resolve_or_explain(str(filled["payee"]))
        if pcode:
            return ShapeResult(filled=filled, error_code=pcode, message="该收款人不能用于这笔 AA")
        filled["payee_id"] = person["payee_id"] if person else None
    if not filled.get("payee_id"):
        missing.append("payee")
        clarify = clarify or WHO_CLARIFY
    if chosen is None:                                   # 多笔且没说哪一笔 → 追问
        return ShapeResult(filled=filled, missing=missing, clarify=clarify)
    if "payee" in missing:
        return ShapeResult(filled=filled, missing=missing, clarify=clarify)
    share = next((s for s in chosen["shares"] if s["payee_id"] == filled["payee_id"]), None)
    if share is None:
        return ShapeResult(filled=filled, error_code="NOT_FOUND",
                           message="这个人不在这一笔 AA 里")
    if share["status"] == "paid":
        return ShapeResult(filled=filled, error_code="INVALID_STATE",
                           message=f"{share['payee_name']} 已经登记过已付了")
    filled["request_id"] = chosen["request_id"]
    filled["payer_name"] = share["payee_name"]
    return ShapeResult(filled=filled)


def _shape_cancel(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：撤销哪一笔 AA（给了 request_id 就直达）。"""
    filled = dict(slots)
    items, code = _active()
    if code is not None:
        return ShapeResult(filled=filled, error_code=code, message="取不到您的 AA 请求")
    if filled.get("request_id"):
        chosen = next((it for it in items if it["request_id"] == filled["request_id"]), None)
        if chosen is None:
            return ShapeResult(filled=filled, error_code="NOT_FOUND", message="这笔 AA 已不在进行中")
    elif not items:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="您没有进行中的 AA 收款")
    elif len(items) > 1:
        return ShapeResult(filled=filled, missing=["request_id"], clarify=PICK_AMBIGUOUS)
    else:
        chosen = items[0]
    filled["request_id"] = chosen["request_id"]
    return ShapeResult(filled=filled)


def _prepare(filled: dict, tier: str) -> Prepared:
    """只读那一笔（不调工具）：给卡面提供事由/合计/已付人数。"""
    items, code = _active()
    if code is not None:
        return Prepared(ok=False, error_code=code, message="取不到您的 AA 请求")
    row = next((it for it in items if it["request_id"] == filled.get("request_id")), None)
    if row is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="这笔 AA 已不在进行中")
    return Prepared(ok=True, facts={**row, "tier": tier}, credential="")


def _prepare_mark_paid(filled: dict) -> Prepared:
    return _prepare(filled, MARK_TIER)


def _prepare_cancel(filled: dict) -> Prepared:
    return _prepare(filled, CANCEL_TIER)


def _card(intent: str, filled: dict, facts: Mapping, tier: str,
          requires_otp: bool) -> confirm_card.CardInput:
    """登记已付 / 撤销 AA 的确认卡。"""
    marking = intent == "aa_mark_paid"
    rows = [("事由", str(facts["title"])),
            ("合计", f"{facts['total_yuan']} 元"),
            ("已付", f"{facts['paid_count']} / {facts['share_count']} 人")]
    if marking:
        rows.insert(0, ("已付的人", str(filled.get("payer_name") or "")))
    else:
        rows.append(("说明", "已登记的付款记录保留，不回滚"))
    return confirm_card.CardInput(
        title="登记已付确认卡" if marking else "撤销 AA 确认卡",
        intent_cn="登记已付" if marking else "撤销 AA",
        rows=rows, tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "登记已付" if marking else "撤销 AA"))


def _plain(intent: str, filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（降级回执）。"""
    marking = intent == "aa_mark_paid"
    return (f"请确认：{'登记 ' + str(filled.get('payer_name')) + ' ' if marking else ''}"
            f"{'已付' if marking else '撤销'}「{facts.get('title')}」这笔 AA"
            f"（合计 {facts.get('total_yuan')} 元，已付 {facts.get('paid_count')}/"
            f"{facts.get('share_count')} 人），权限档 {tier}。")


def _execute_mark_paid(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T43** 登记已付。L1，无 OTP。"""
    del otp
    from tools import _aa_request
    return _aa_request.mark_aa_share_paid(str(confirmation.filled["request_id"]),
                                          str(confirmation.filled["payee_id"]))


def _execute_cancel(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T44** 撤销。L1，无 OTP。"""
    del otp
    from tools import _aa_request
    return _aa_request.cancel_aa_request(str(confirmation.filled["request_id"]))
