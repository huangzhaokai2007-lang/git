"""写意图描述符表（card-24 从 `write_flow.py` 拆出）：把「转账专属」的写路径泛化成
「每个写意图声明自己的形状」，新增写功能只需加一条描述符。

**为什么单独一个文件**：`agent/write_flow.py` 与 `agent/orchestrator.py` 都已逼近单文件 300 行红线
（单文件 ≤300、单函数 ≤40），per-intent 的差异必须外置；`orchestrator` 只 import 本模块，
**源码里不出现任何工具名字面量**（`tests/test_orchestrator_readonly.py` 的静态红线）。

**两种凭证（card-24 台账口径，不得混用）**：

| 凭证 | 签发方 | 使用者 | OTP 校验位置 |
|---|---|---|---|
| `preview_token` | 工具层 `preview_*`（T7） | 转账 | **工具层** |
| `confirm_ref` | 工具层私有约定 `issue_confirm_ref`（T11/T12/T15） | 订阅取消 | **编排层** |

`confirm_ref` 族的工具签名**没有 otp 参数**（`cancel_subscription(sub_id, confirm_ref)`），
故 OTP 闸门落在编排层 —— 依据 `tools/cross_scene.py`「确认与 OTP 落在编排层（同 T11/T15 的分工）」。

**新增写意图的步骤**：① 在 `_SPECS` 加一条 `WriteSpec`；② 实现五个纯函数；
③ 在 `tests/cases/orchestrator.yaml` 加用例。**动到转账相关代码时，既有 trf-* 用例是硬判据。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from agent import confirm_card
from tools import subscription, transfer
from tools.schemas import ToolResult
from tools.transfer import OTP_CODE                    # agent→tools 合法；OTP 明文绝不进日志/facts/回执

#: 金额文本形状：最多两位小数的十进制（禁科学计数/负号/多余位数）
_YUAN = re.compile(r"\d+(?:\.\d{1,2})?")

#: 转账类的中文名与卡标题（只做措辞，不含任何业务数字）
TRANSFER_CN = {"transfer_single": "单笔转账", "transfer_scheduled": "定时转账"}

#: 订阅取消（T11）：工具层的动作名必须与 `tools/subscription.CANCEL_ACTION` 同值
CANCEL_ACTION = "cancel_subscription"
CANCEL_CN = "订阅取消"


def _code_of(result: ToolResult) -> str | None:
    """工具结果的错误码字符串（枚举与裸字符串都接受）。"""
    code = result.error_code
    return str(getattr(code, "value", code)) if code else None


@dataclass(frozen=True)
class ShapeResult:
    """SLOT_FILL 的结果：补齐的槽位 / 缺槽（追问）/ 直接报错（零命中）。"""

    filled: dict
    missing: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class Prepared:
    """PRECHECK 的输入：工具层事实包 + **工具层签发**的凭证（编排层不得自己造串）。"""

    ok: bool
    facts: Mapping = field(default_factory=dict)
    credential: str = ""
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class WriteSpec:
    """一个写意图的完整形状（写路径四步里「因意图而异」的那部分）。"""

    intent: str
    intent_cn: str
    title: str                        # 确认卡标题（不含书名号）
    credential_kind: str              # "preview_token" | "confirm_ref"
    otp_in_tool: bool                 # True=工具层校验 OTP；False=编排层校验
    missing: tuple[str, ...]          # 缺槽时回给用户的槽位名（CLARIFY 措辞用）
    tools: tuple[str, ...]            # PRECHECK 阶段调用的工具（审计 tool_calls）
    execute_tool: str                 # EXECUTE 阶段的工具名
    target_key: str                   # 工具层主键在槽位里的键名（`payee_id` / `sub_id`）
    what: str                         # 中文宾语（「这笔{what}」）
    shape: Callable[[Mapping], ShapeResult]
    prepare: Callable[[dict], Prepared]
    card: Callable[[str, dict, Mapping, str, bool], confirm_card.CardInput]
    plain: Callable[[dict, Mapping, str], str]
    execute: Callable[[str, str, str | None], ToolResult]


# ---------------- 转账（T6/T7）：preview_token 族，OTP 在工具层 ----------------

def yuan_to_cents(value: object) -> int | None:
    """元 → 整数分：**字符串拆分整数运算，禁 float**（analyst 裁决；避免 0.1 类精度问题）。

    认不出或非正数 → None（由调用方判为缺槽并追问）。
    """
    text = str(value).strip().replace(",", "").replace("元", "")
    if not _YUAN.fullmatch(text):
        return None
    whole, _, frac = text.partition(".")
    cents = int(whole) * 100 + int((frac + "00")[:2])
    return cents if cents > 0 else None                              # 非正数 → 视为缺槽（不猜 0 元）


def _shape_transfer(slots: Mapping) -> ShapeResult:
    """SLOT_FILL 对齐工具层签名：`payee`（名字/手机号）→ `payee_id`；`amount`（元）→ 整数分。

    没找到或同名多个 → 记缺槽（**绝不擅自选一个**）；金额认不出 → 记缺槽。
    """
    filled, missing = dict(slots), []
    if not filled.get("payee_id"):
        query = str(filled.get("payee") or "").strip()
        found = transfer.resolve_payee(query) if query else None
        candidates = (found.facts.get("candidates") or []) if found is not None and found.ok else []
        if len(candidates) == 1:
            filled["payee_id"] = candidates[0]["id"]
            filled["payee_name"] = candidates[0]["name"]
            filled["masked_phone"] = candidates[0]["phone"]
        else:
            missing.append("payee")
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    return ShapeResult(filled=filled, missing=missing)


def _prepare_transfer(filled: dict) -> Prepared:
    """T7 预览：**只算不执行**，凭证由工具层签发（编排层不造串）。

    事实包补一个 `payee_phone`（脱敏手机号）—— 它来自工具层 `resolve_payee` 的候选，不是编排层编的；
    确认卡上要显示它，`verify_numbers` 必须能在事实包里找到（铁律 2）。
    """
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"])
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return Prepared(ok=True, facts={**preview.facts, "payee_phone": masked},
                    credential=str(preview.data["preview_token"]))


def _card_transfer(intent: str, filled: dict, facts: Mapping, tier: str,
                   requires_otp: bool) -> confirm_card.CardInput:
    """转账确认卡：金额、档位、风险因子**全部来自 facts**（卡面行与 card-10 逐字一致）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return confirm_card.CardInput(
        title="转账确认卡", intent_cn=TRANSFER_CN.get(intent, intent),
        rows=[("收款人", f"{facts['payee_name']}（{masked}）"),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("预计到账", confirm_card.expected_arrival(tier))],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note(list(facts.get("factors") or [])))


def _plain_transfer(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本：确认卡出现 facts 之外的数字时的降级回执（只引用传入值）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return (f"请确认：向 {facts['payee_name']}（{masked}）转账 {facts['amount_yuan']} 元，"
            f"权限档 {tier}。")


def _execute_transfer(target_id: str, credential: str, otp: str | None) -> ToolResult:
    """OTP 校验与幂等**都在工具层**（card-05 已 PASS）；本层不重写。"""
    del target_id                                                    # 工具层只认 token
    return transfer.execute_transfer(credential, otp)


# ---------------- 订阅取消（T11）：confirm_ref 族，OTP 在编排层 ----------------

def _shape_subscription(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`merchant` → `sub_id`（照 `resolve_payee` 先例）。

    唯一命中 → 补 `sub_id`；**重名 → 追问**（CLARIFY）；**零命中 → 报错**（NOT_FOUND）。
    这两条是 F04 的验收点，与转账「一律追问」的口径不同。
    """
    filled = dict(slots)
    if filled.get("sub_id"):
        return ShapeResult(filled=filled)
    query = str(filled.get("merchant") or "").strip()
    if not query:
        return ShapeResult(filled=filled, missing=["merchant"])
    listed = subscription.list_subscriptions()
    if not listed.ok:
        return ShapeResult(filled=filled, error_code=_code_of(listed), message=listed.message)
    items = listed.facts.get("items") or []
    hits = [item for item in items if query in str(item.get("merchant") or "")]
    if not hits:
        return ShapeResult(filled=filled, error_code="NOT_FOUND", message="找不到匹配的订阅")
    if len(hits) > 1:
        return ShapeResult(filled=filled, missing=["merchant"])       # 重名：追问，绝不擅自选一个
    filled["sub_id"], filled["merchant"] = hits[0]["id"], hits[0]["merchant"]
    return ShapeResult(filled=filled)


def _prepare_subscription(filled: dict) -> Prepared:
    """订阅取消**没有** `preview_*`（T11 签名就是 `(sub_id, confirm_ref)`）：

    事实包取自 T10 的列表（F19 为 F04 提供事实 —— 卡面数字必须有来源），
    凭证由工具层私有约定 `issue_confirm_ref` 签发，绑定 `(action + target_id + 当前用户)`。
    """
    sub_id = filled["sub_id"]
    listed = subscription.list_subscriptions()
    if not listed.ok:
        return Prepared(ok=False, error_code=_code_of(listed), message=listed.message)
    item = next((row for row in (listed.facts.get("items") or []) if row.get("id") == sub_id), None)
    if item is None:
        return Prepared(ok=False, error_code="NOT_FOUND", message="找不到该订阅")
    facts = {"merchant": item["merchant"], "cycle": item["cycle"],
             "next_charge_date": item["next_charge_date"], "amount_yuan": item["amount_yuan"]}
    return Prepared(ok=True, facts=facts,
                    credential=subscription.issue_confirm_ref(CANCEL_ACTION, sub_id))


def _cycle_cn(cycle: object) -> str:
    """周期中文（与 `tools/subscription.CYCLE_LABELS` 同源，缺省原样回显）。"""
    return subscription.CYCLE_LABELS.get(str(cycle), str(cycle))


def _card_subscription(intent: str, filled: dict, facts: Mapping, tier: str,
                       requires_otp: bool) -> confirm_card.CardInput:
    """订阅取消确认卡：商户、金额、周期、下次扣费日**全部来自 facts**（T10 的事实包）。"""
    return confirm_card.CardInput(
        title="订阅取消确认卡", intent_cn=CANCEL_CN,
        rows=[("商户", str(facts["merchant"])),
              ("金额", f"{facts['amount_yuan']} 元／{_cycle_cn(facts['cycle'])}"),
              ("下次扣费", str(facts["next_charge_date"]))],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], "取消订阅"))


def _plain_subscription(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（订阅版降级回执）。"""
    return (f"请确认：取消订阅 {facts['merchant']}（{facts['amount_yuan']} 元／"
            f"{_cycle_cn(facts['cycle'])}），权限档 {tier}。")


def _execute_subscription(target_id: str, credential: str, otp: str | None) -> ToolResult:
    """T11 的 OTP 闸门**在编排层**（工具签名没有 otp 参数），本函数只把 `(sub_id, ref)` 交给工具层。"""
    del otp
    return subscription.cancel_subscription(target_id, credential)


# ---------------- 描述符表（唯一来源） ----------------

_SPECS: dict[str, WriteSpec] = {
    "transfer_single": WriteSpec(
        intent="transfer_single", intent_cn="单笔转账", title="转账确认卡",
        credential_kind="preview_token", otp_in_tool=True,
        missing=("payee", "amount"), tools=("preview_transfer",), execute_tool="execute_transfer",
        target_key="payee_id",
        what="转账", shape=_shape_transfer, prepare=_prepare_transfer,
        card=_card_transfer, plain=_plain_transfer, execute=_execute_transfer),
    "transfer_scheduled": WriteSpec(
        intent="transfer_scheduled", intent_cn="定时转账", title="转账确认卡",
        credential_kind="preview_token", otp_in_tool=True,
        missing=("payee", "amount"), tools=("preview_transfer",), execute_tool="execute_transfer",
        target_key="payee_id",
        what="转账", shape=_shape_transfer, prepare=_prepare_transfer,
        card=_card_transfer, plain=_plain_transfer, execute=_execute_transfer),
    "subscription_cancel": WriteSpec(
        intent="subscription_cancel", intent_cn=CANCEL_CN, title="订阅取消确认卡",
        credential_kind="confirm_ref", otp_in_tool=False,
        missing=("merchant",), tools=("list_subscriptions",), execute_tool=CANCEL_ACTION,
        target_key="sub_id",
        what="取消订阅", shape=_shape_subscription, prepare=_prepare_subscription,
        card=_card_subscription, plain=_plain_subscription, execute=_execute_subscription),
}

#: 已接通的写意图（`write_flow.WRITE_INTENTS` 引它 —— 单一来源，避免两份清单漂移）
WRITE_INTENTS: tuple[str, ...] = tuple(_SPECS)


def spec_of(intent: str) -> WriteSpec | None:
    """取意图的写描述符；未接通返回 None（调用方按「未接通」处理）。"""
    return _SPECS.get(intent)


def otp_ok(otp: str | None) -> bool:
    """编排层的 OTP 比对（**只给 `confirm_ref` 族用**；`preview_token` 族由工具层判）。

    常量引自 `tools.transfer.OTP_CODE`（规格 §5：demo 固定 `123456`），
    `tools/card.OTP_CODE` 与它同值（有测试钉着）；OTP 明文**绝不进日志/facts/回执**。
    """
    return str(otp or "").strip() == OTP_CODE
