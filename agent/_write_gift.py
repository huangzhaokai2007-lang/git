"""送礼计划族写描述符（T16 写 + T19 只读预览）：**无凭证族**，L2（要 OTP）。

F20 卡新增（核心契约类型见 `agent/_write_common.py`，注册表见 `agent/write_intents.py`）。

**F20 设计里人类已批的 Q1–Q5 落在哪一处**：

| 决定 | 落点 |
|---|---|
| Q1 确认卡显示预算与礼物清单 | `_card_gift` 的卡面行 |
| Q2 **新增 T19 `preview_gift`**（L0 只读） | `_prepare_gift` 调它取事实包 |
| Q4 写路径 `execute` 传整张确认卡 | `_execute_gift` 从 `confirmation.filled` 取 `date`（`target_id` 装不下） |
| Q5 取消/释放资金**本期不做** | 本模块不提供任何撤销/释放入口；缺口登记在 F20 设计文档 |

**口径逐条对齐 F20 验收清单**：

| 验收点 | 怎么满足 |
|---|---|
| 确认与 OTP 前**不锁定资金**、不生成模拟预订 | `prepare` 只调 T19（只读），**不调** T16 `plan_gift`（它才锁款 + 生成 `plan_id`/`lock_id`） |
| 说明 `available` 与实际支出的区别 | 卡面把「预算 / 合计 / 可用余额 / 账户余额」并列，并印一行资金说明 |
| 重复提交不重复锁款 | T16 签名无 `preview_token`/`confirm_ref` ⇒ 无工具层幂等，靠确认卡一次性消费（同 T9/T16 口径） |
| 失败回滚 | T16 内为单一事务（锁款 + 审计同事务），任一失败整笔回滚；本模块不改这条 |
"""

from __future__ import annotations

from typing import Mapping

from agent import confirm_card, payee_resolve, period
from agent._write_common import Prepared, ShapeResult, _code_of, yuan_to_cents
from tools import cross_scene
from tools.schemas import ToolResult

GIFT_CN = "送礼计划"


def _blacklist_reason(contact: str) -> str:
    """收货人若命中**黑名单** → 返回拒绝文案（否则空串）。只按姓名/标签解析，不猜。"""
    if not contact:
        return ""
    person, _code, _facts = payee_resolve.resolve_or_explain(contact, allow_blacklisted=True)
    if person is None or not person.get("blacklist"):
        return ""
    reason = str(person.get("blacklist_reason") or "")
    text = f"{person['name']} 已在黑名单，不能送礼。"
    return text + (f"原因：{reason}。" if reason else "")


def _shape_gift(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`contact` 原样用；`date` 归一到 `YYYY-MM-DD`；`budget`（元）→ 整数分。

    **日期复用 `agent/period.resolve_day`**（全仓唯一一份日期口径：ISO 原样、相对月取首日）；
    认不出（如「明天」「下周一」）→ 当缺槽追问，**绝不猜一个日期出来**。
    ⚠️ 已知限制：相对**日**不在 `period.py` 的支持范围（它只支持 ISO 与相对月），这是共享缺口
    （读路径同样如此），扩展相对日属另一张卡 —— 已在 PR 的「剩余限制」登记。

    **「不能早于基准日」留给工具层判**（T19 的 `_wanted_date`）：编排层只判「有没有」，
    不复制这类业务口径 —— 复制就是第二份实现，迟早与工具层分叉。
    """
    filled, missing = dict(slots), []
    contact = str(filled.get("contact") or "").strip()
    #: 2026-10-24 口径（人类原文）：「黑名单无法进行转账、定时转账、送礼，直接拒绝，无需验证码」。
    #: 只按**收款人姓名/标签**解析（收货人可能不在收款人里 → 不拦），不猜。
    blocked = _blacklist_reason(contact)
    if blocked:
        return ShapeResult(filled=filled, error_code="FORBIDDEN", message=blocked)
    if not contact:
        missing.append("contact")
    day = period.resolve_day(filled.get("date"), edge="start") if filled.get("date") else None
    if day is None:
        missing.append("date")
    else:
        filled["date"] = day
    # 键名必须用 `amount_cents`：`write_flow.start` 从 `filled["amount_cents"]` 取本次写操作的
    # 金额（转账 / AA 同此约定），`budget` 只是用户原话里的槽位名。写成 `budget_cents` 会让
    # `Confirmation.amount_cents` 恒为 0，T16 收到预算 0 → INVALID_ARGUMENT（实测踩过）。
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("budget")) if filled.get("budget") is not None else None
        if cents is None:
            missing.append("budget")
        else:
            filled["amount_cents"] = cents
    return ShapeResult(filled=filled, missing=missing)


def _prepare_gift(filled: dict) -> Prepared:
    """**只算不执行**：调 T19 `preview_gift` 拿事实包（清单 / 合计 / 可用余额），**不调 T16**。

    T16 一调就锁款并生成 `plan_id`/`lock_id` —— 「确认与 OTP 前不锁定资金、不生成模拟预订」
    由此在编排层被结构性保证（而不是靠自觉）。
    """
    #: v2：送礼黑名单（与转账同一套口径）—— 收货人在黑名单 → 直接拒，不出确认卡。
    _, code, rfacts = payee_resolve.resolve_or_explain(str(filled.get("contact") or ""))
    if code:
        return Prepared(ok=False, error_code=code, message=str(rfacts.get("message") or ""))
    preview = cross_scene.preview_gift(str(filled["contact"]), str(filled["date"]),
                                       int(filled["amount_cents"]))
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    facts = dict(preview.facts)
    facts["target_name"] = str(filled["contact"])     # 卡面主对象（`Confirmation.target_name` 取它）
    return Prepared(ok=True, facts=facts, credential="")     # T16 无凭证参数


def _card_gift(intent: str, filled: dict, facts: Mapping, tier: str,
               requires_otp: bool) -> confirm_card.CardInput:
    """送礼确认卡：联系人 / 日期 / 预算 / 清单（含单价）/ 合计 / 可用余额 / 账户余额。

    数字**全部来自 T19 的事实包**（铁律 2）；「可用余额 vs 账户余额」并列是 F20 验收第 2 条的要求。
    """
    listed = "、".join(f"{item['name']} {item['price_yuan']} 元" for item in facts["items"])
    return confirm_card.CardInput(
        title="送礼计划确认卡", intent_cn=GIFT_CN,
        rows=[("联系人", str(facts["contact"])),
              ("预订日期", str(facts["date"])),
              ("预算", f"{facts['budget_yuan']} 元"),
              ("礼物清单", listed),
              ("合计", f"{facts['total_yuan']} 元"),
              ("可用余额", f"{facts['available_yuan']} 元"),
              ("账户余额", f"{facts['balance_yuan']} 元"),
              ("资金说明", "锁定只减可用余额，账户余额不变（不是支出）")],
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], GIFT_CN))


def _plain_gift(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（送礼版降级回执）。"""
    listed = "、".join(str(item["name"]) for item in facts.get("items") or [])
    return (f"请确认：为 {facts.get('contact')} 预订 {facts.get('date')} 的礼物（{listed}），"
            f"合计 {facts.get('total_yuan')} 元，可用余额 {facts.get('available_yuan')} 元，"
            f"权限档 {tier}。")


def _execute_gift(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """T16 真锁款 + mock 预订：**无凭证**（T16 签名如此），故直接用槽位快照。

    金额取 `confirmation.amount_cents`（= 预算分），日期取槽位快照 —— `target_id` 只装得下联系人，
    这也是 F20 设计 Q4 要改写路径签名的原因。
    """
    del otp
    result = cross_scene.plan_gift(str(confirmation.filled["contact"]),
                                 str(confirmation.filled["date"]),
                                 int(confirmation.amount_cents))
    if result.ok:                                        # F20 追加（T38）：落一条可查的订单
        from tools import _gift_order
        facts = result.facts or {}
        order = _gift_order.record_gift_order(
            str(facts.get("plan_id") or ""), str(facts.get("lock_id") or ""),
            str(facts.get("contact") or ""), str(facts.get("date") or ""),
            int(facts.get("budget") or 0), int(facts.get("total") or 0),
            list(facts.get("items") or []))
        if order.ok:
            result = result.model_copy(update={
                "message": f"{result.message} 订单号 {order.data['order_id']}。"})
    return result
