"""转账族写描述符（T6/T7）：`preview_token` 族，**OTP 校验与幂等都在工具层**。

F15/F20 拆卡时从 `agent/write_intents.py` 按意图族拆出；核心契约类型与共享工具见
`agent/_write_common.py`，注册表见 `agent/write_intents.py`。

**本族覆盖两个意图**：`transfer_single`（即时）与 `transfer_scheduled`（**F16 定时转账**）。

F16 的关键差异只有两处：① PRECHECK 时把 `schedule` 一起传给 T7（此前不传，导致「明天转」被当即时转账）；
② EXECUTE 走 **T21 `schedule_transfer`**（落 `pending` 预约、**不扣款**），而不是 T8 立即扣款。
到点执行由 `agent/scheduled_runner.py` 的惰性结算调 T22 完成。
方案见 `docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md`（Q1–Q10 人类已批）。
"""

from __future__ import annotations

import datetime
import re
from typing import Mapping

from agent import card_policy, confirm_card, payee_clarify, payee_resolve, payee_risk
from agent import _relative_time                              # 相对时间说法（明天/下周一/月底）
from agent._write_common import repeat_cycle, Prepared, ShapeResult, _code_of, normalize_query, yuan_to_cents
from tools import transfer

#: 转账类的中文名与卡标题（只做措辞，不含任何业务数字）
TRANSFER_CN = {"transfer_single": "单笔转账", "transfer_scheduled": "定时转账"}

#: 预约时间合法性的三个界（人类规格「过去 / 5 分钟内 / 超过 1 年，均拒绝并提示」）
SCHEDULE_MIN_AHEAD = datetime.timedelta(minutes=5)
SCHEDULE_MAX_AHEAD = datetime.timedelta(days=365)

#: 预约时间认不出时的追问（纯措辞，不含业务数字）。相对说法已支持（见 `_relative_time`）。
SCHEDULE_CLARIFY = ("请说明预约时间，格式如 2026-10-21 09:00（也接受 2026-10-21），"
                    "或直接说「明天」「下周一」「下个月 5 号」「月底」这类说法。")


def _iso_schedule(value: object) -> str | None:
    """预约时间：先按 ISO8601 解析，失败再按**相对说法**（明天/下周一/月底…）解析。

    两者都认不出 → `None`（当缺槽追问，绝不猜）。
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text).isoformat(timespec="minutes")
    except ValueError:
        return _relative_time.parse(text, transfer._now())


def _schedule_guard(when: str) -> str:
    """预约时间合法性：过去 / 5 分钟内 / 超过 1 年 → 返回拒绝文案（否则空串）。

    依据人类规格「时间合法性校验：过去时间、5 分钟内、超过 1 年，均拒绝并提示」。
    """
    moment = datetime.datetime.fromisoformat(when)
    now = transfer._now()
    if moment <= now:
        return f"预约时间 {when.replace('T', ' ')} 已经过去了，请换一个将来的时间。"
    if moment - now < SCHEDULE_MIN_AHEAD:
        return "预约时间太近了（5 分钟内），请至少提前 5 分钟；想马上转请用「即时转账」。"
    if moment - now > SCHEDULE_MAX_AHEAD:
        return "预约时间超过 1 年了，请换一个 1 年内的日期。"
    return ""


#: 「每月 15 日」「每个月 15 号」里的日号（1..31）
_MONTH_DAY_HINT = re.compile(r"(\d{1,2})\s*[日号]")


def _day_of_month(hint: object) -> int | None:
    """从循环原话里抠出「每月 N 日」的 N；抠不到或越界 → None（不猜）。"""
    match = _MONTH_DAY_HINT.search(str(hint or ""))
    if match is None:
        return None
    day = int(match.group(1))
    return day if 1 <= day <= 31 else None


def _first_occurrence(cycle: str, hint: object) -> str:
    """循环预约**没给日期**时推导首期：先看原话，再按周期取缺省。

    2026-10-24 修（实测）：「每月 15 日给房东转 30」被算成**月末**（10-31）。
    原因是原话里只有「15 日」，`_relative_time` 的「N 号」规则要求带「本月/下个月」前缀。
    现在补一条：**monthly + 原话里带 N 日/号** → 本月 N 日（还没到）否则次月 N 日。
    """
    from_hint = _relative_time.parse(hint, transfer._now())
    if from_hint:
        return from_hint
    if cycle == "monthly" and (day_of_month := _day_of_month(hint)):
        today = transfer._now()
        first = _relative_time.month_day(today, day_of_month)
        if first <= today.date():                       # 本月这天已过 → 排到下月同日
            first = _relative_time.month_day(today, day_of_month, month_shift=1)
        return first.isoformat() + "T09:00"
    if cycle == "monthly":
        day = _relative_time.next_month_end(transfer._now())
    else:                                                    # weekly：从今天起 +7 天
        day = (transfer._now() + datetime.timedelta(days=7)).date()
    return datetime.datetime(day.year, day.month, day.day, 9, 0).isoformat(timespec="minutes")


def _shape_scheduled(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：收款人 / 金额沿用转账口径，另加 `schedule`（ISO 或相对说法）。

    收款人解析与金额换算**直接复用 `_shape_transfer`**（同一份实现，不复制）—— 同名追问、
    脱敏手机号透出等 F17 行为因此自动一致。

    **循环预约可以不给日期**：「每个月月末给房东转 300」→ 按周期推出首期（最近月末），
    不再追问格式（实测缺陷：以前会问「请说明预约时间」）。
    """
    shaped = _shape_transfer(slots)
    filled, missing = dict(shaped.filled), list(shaped.missing)
    when = _iso_schedule(filled.get("schedule"))
    if when is not None:                                      # 2026-10-24：时间合法性三界
        error = _schedule_guard(when)
        if error:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT", message=error)
    cycle = repeat_cycle(filled.get("repeat"))               # 「每个月」→ monthly（认不出 = 一次性）
    if filled.get("repeat_until"):                           # 循环结束日（「到年底」等相对说法也认）
        end = _iso_schedule(filled["repeat_until"])
        filled["repeat_until"] = end[:10] if end else None
        if end is None:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT",
                               message="循环结束日看不懂，请用 2026-12-31 这样的日期")
    if when is None and cycle:
        when = _first_occurrence(cycle, filled.get("repeat"))
        filled["first_auto"] = True                          # 卡面标注「首期为自动推算」
    if when is None:
        missing.append("schedule")
    else:
        filled["schedule"] = when
    if cycle:
        filled["repeat_cycle"] = cycle
    clarify = shaped.clarify or (SCHEDULE_CLARIFY if "schedule" in missing else "")
    return ShapeResult(filled=filled, missing=missing, clarify=clarify)


def _payee_query(raw: object) -> str:
    """把「模型回显的卡面串」还原成可解析的查询键（实现见 `_write_common.normalize_query`）。

    实测坑：追问时界面列 `李四（139****1001）`，模型会**原样回显**这个串，而 `resolve_payee`
    按子串匹配 name / phone / bank —— 整串匹配不上，于是「回 1001 仍解析不出收款人」。
    """
    return normalize_query(raw)


def _shape_transfer(slots: Mapping) -> ShapeResult:
    """SLOT_FILL 对齐工具层签名：`payee`（名字/手机号）→ `payee_id`；`amount`（元）→ 整数分。

    收款人解析走 v2 的**统一关卡**（`agent/payee_resolve.py`）—— 标签、同音、黑名单一套口径，
    转账与定时转账共用这里（`_shape_scheduled` 复用本函数）。追问仍由 `payee_clarify.ask_for`
    生成，且要**先收齐所有缺槽再一次性问**（「缺人 + 缺金额」两件事不互相盖掉）。
    """
    filled, missing, payee_facts = dict(slots), [], {}
    if not filled.get("payee_id"):
        query = _payee_query(filled.get("payee"))
        if query:                                        # 有查询词才走关卡；空 → 留在 missing 用通用追问
            person, code, facts = payee_resolve.resolve_or_explain(query)
            if code:                                     # 黑名单：直接拒，不出确认卡
                return ShapeResult(filled=filled, error_code=code,
                                   message=str(facts.get("message") or ""))
            if person is None:
                missing.append("payee")
                payee_facts = facts
            else:
                filled["payee_id"] = person["payee_id"]
                filled["payee_name"] = person["name"]
                filled["masked_phone"] = person["masked_phone"]
        else:
            missing.append("payee")
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None:
            missing.append("amount")
        else:
            filled["amount_cents"] = cents
    #: 2026-10-31（用户要求）：**必须选收款人的银行卡**（取消"默认卡"自动带过）。
    #: 槽位 `payee_card` 装的是卡 id；这里统一映射到工具层参数名 `payee_card_id`。
    if filled.get("payee_card") and not filled.get("payee_card_id"):
        filled["payee_card_id"] = str(filled["payee_card"])
    clarify = payee_clarify.ask_for(payee_facts, missing) if payee_facts else ""
    return ShapeResult(filled=filled, missing=missing, clarify=clarify,
                       candidates=list(payee_facts.get("candidates") or []))


def _prepare_transfer(filled: dict) -> Prepared:
    """T7 预览：**只算不执行**，凭证由工具层签发（编排层不造串）。

    事实包补一个 `payee_phone`（脱敏手机号）—— 它来自工具层 `resolve_payee` 的候选，不是编排层编的；
    确认卡上要显示它，`verify_numbers` 必须能在事实包里找到（铁律 2）。

    **只管即时转账**（`transfer_single`）：`transfer_scheduled` 走另一条准备函数
    `_prepare_scheduled`（F16 已实现：落 `scheduled_transfer` 待执行行、到点才扣款），
    两条路径分开，本函数不传 `schedule` 是**设计如此**，不是缺口。
    """
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"],
                                       payee_card_id=filled.get("payee_card_id"))
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    #: v2 专属额度层：黑名单已在 `_shape_transfer` 拦下，这里只覆盖白名单额度——
    #: 额度内维持既有档位（L1 免码），超额度/未设额度至少升到 L2 要码。
    person = payee_resolve.profile_of(filled["payee_id"])
    tier, quota_note = payee_risk.apply_quota(person or {}, filled["amount_cents"],
                                              str(preview.facts.get("tier") or "L2"),
                                              factors=preview.facts.get("factors") or [])
    return Prepared(ok=True, facts={**preview.facts, "tier": tier, "payee_phone": masked,
                                    "quota_note": quota_note},
                    credential=str(preview.data["preview_token"]))


def _card_transfer(intent: str, filled: dict, facts: Mapping, tier: str,
                   requires_otp: bool) -> confirm_card.CardInput:
    """转账确认卡：金额、档位、风险因子**全部来自 facts**（卡面行与 card-10 逐字一致）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    note = confirm_card.risk_note(list(facts.get("factors") or []))
    if facts.get("quota_note"):
        note = "；".join(x for x in (note, str(facts["quota_note"])) if x)
    return confirm_card.CardInput(
        title="转账确认卡", intent_cn=TRANSFER_CN.get(intent, intent),
        rows=[("收款人", f"{facts['payee_name']}（{masked}）"),
              *([("收款卡", f"{facts['payee_card_bank']} {facts['payee_card_mask']}")]
                if facts.get("payee_card_mask") else []),
              ("金额", f"{facts['amount_yuan']} 元"),
              ("预计到账", confirm_card.expected_arrival(tier)),
              #: 2026-10-24 人类规格「转账卡片：转账后余额 / 今日剩余额度」——
              #: 两个数都直接来自 T7 的事实包（`balance_after` / `limits_daily_remaining`），不重算。
              *([("转账后余额", f"{facts['balance_after_yuan']} 元")]
                if facts.get("balance_after_yuan") else []),
              *([("今日剩余额度", f"{facts['limits_daily_remaining_yuan']} 元")]
                if facts.get("limits_daily_remaining_yuan") else [])],
        tier=tier, requires_otp=requires_otp,
        risk_note=note)


def _plain_transfer(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本：确认卡出现 facts 之外的数字时的降级回执（只引用传入值）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return (f"请确认：向 {facts['payee_name']}（{masked}）转账 {facts['amount_yuan']} 元，"
            f"权限档 {tier}。")


def _execute_transfer(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """OTP 校验与幂等**都在工具层**（card-05 已 PASS）；本层不重写。"""
    return transfer.execute_transfer(confirmation.credential, otp)   # 工具层只认 token，不看 payee_id


# ---------------- 定时转账（F16）：预约时间进卡面、执行只落预约 ----------------

def _prepare_scheduled(filled: dict) -> Prepared:
    """T7 预览（**只算不执行**）：把 `schedule` 一起传进去 —— 这正是 F16 修掉的那个洞。

    预约**尚未落库**（落库在 EXECUTE 步调 T21）。事实包里额外放 `schedule` 字符串是必须的：
    卡面要印预约时间，而 `card_numbers_outside_facts` 会把「2026」「09」这类数字当成 facts
    之外的数字 → 整张卡降级（铁律 2 守门），故预约时间必须可溯源。
    """
    preview = transfer.preview_transfer(filled["payee_id"], filled["amount_cents"],
                                        schedule=str(filled["schedule"]),
                                        payee_card_id=filled.get("payee_card_id"))
    if not preview.ok:
        return Prepared(ok=False, error_code=_code_of(preview), message=preview.message)
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    #: v2 专属额度层（与即时转账同一份口径）：超额度/未设额度 → 至少 L2 要码。
    person = payee_resolve.profile_of(filled["payee_id"])
    tier, quota_note = payee_risk.apply_quota(person or {}, filled["amount_cents"],
                                              str(preview.facts.get("tier") or "L2"),
                                              factors=preview.facts.get("factors") or [])
    facts = {**preview.facts, "tier": tier, "payee_phone": masked,
             "schedule": str(filled["schedule"]), "quota_note": quota_note}
    #: 2026-10-30（用户实测「产生了 bug」）：同收款人 + 同金额 + **同一天**已经有一笔待执行预约时，
    #: 卡面给一行警示（不阻止 —— 可能你就是要再建一笔），避免"悄悄多出一笔"。
    duplicate = _duplicate_schedule(filled, facts)
    if duplicate:
        facts["duplicate_hint"] = duplicate
    return Prepared(ok=True, facts=facts, credential=str(preview.data["preview_token"]))


def _duplicate_schedule(filled: dict, facts: Mapping) -> str:
    """返回重复提示文案（没有重复 → 空串）。只比对**待执行**的预约。"""
    from agent import schedule_flow

    amount = int(facts.get("amount_cents") or 0)
    when = str(facts.get("schedule") or "")[:10]
    name = str(facts.get("payee_name") or "")
    try:
        pending = schedule_flow.pending_schedules()
    except Exception:                              # noqa: BLE001 —— 只读守卫，失败不影响出卡
        return ""
    for item in pending:
        if (str(item.get("payee_name")) == name
                and int(item.get("amount") or item.get("amount_cents") or 0) == amount
                and str(item.get("execute_at"))[:10] == when):
            return (f"你已有一笔相同预约（{str(item['execute_at']).replace('T', ' ')} "
                    f"向 {name} {item.get('amount_yuan')} 元）；确认后会**再建一笔**")
    return ""


def _card_scheduled(intent: str, filled: dict, facts: Mapping, tier: str,
                    requires_otp: bool) -> confirm_card.CardInput:
    """定时转账确认卡：**多一行「预约时间」、且不印「预计到账」**。

    不印「预计到账」是刻意的 —— 那是即时转账的措辞，用在这里会被理解成「马上到账」，
    而 F16 验收第 2 条正是不许把预约当即时。
    """
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    cycle = {"daily": "每日（首期后按天续期）",
             "monthly": "每月（首期后按月续期，月末自动对齐）",
             "weekly": "每周（首期后按周续期）"}.get(str(filled.get("repeat_cycle") or ""), "")
    rows = [("收款人", f"{facts['payee_name']}（{masked}）"),
            ("金额", f"{facts['amount_yuan']} 元"),
            ("预约时间", str(facts["schedule"]).replace("T", " ")),
            ("执行说明", "到预约时间后才扣款，此前不会动钱")]
    if cycle:                                              # 循环预约：方式与结束分两行，更醒目
        rows.insert(3, ("循环方式", cycle))
        if filled.get("repeat_until"):
            rows.insert(4, ("循环结束", f"{filled['repeat_until']}（之后不再续期）"))
    if facts.get("payee_card_mask"):                        # 2026-10-31：转到哪张卡也要印出来
        rows.insert(1, ("收款卡", f"{facts['payee_card_bank']} {facts['payee_card_mask']}"))
    if facts.get("duplicate_hint"):                        # 重复预约：卡面点明，别静默多建一笔
        rows.append(("⚠️ 重复提示", str(facts["duplicate_hint"])))
    return confirm_card.CardInput(
        title="定时转账确认卡", intent_cn="定时转账", rows=rows,
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note(list(facts.get("factors") or [])))


def _plain_scheduled(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（定时转账的降级回执）。"""
    masked = confirm_card.mask_phone(str(filled.get("masked_phone") or ""))
    return (f"请确认：预约 {facts.get('schedule')} 向 {facts['payee_name']}（{masked}）转账 "
            f"{facts['amount_yuan']} 元，权限档 {tier}，到点之前不会扣款。")


def _book_scheduled(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """EXECUTE：调 **T21** 落一条 `pending` 预约（**不扣款**）；带循环 → 再调 **T34** 记周期。

    延迟 import：本模块属编排层，`_transfer_schedule` 属工具层且反过来依赖 `tools.transfer`，
    在函数内导入可彻底避开导入顺序问题（同 `tools/transfer.py` 调 `schedule_args_error` 的做法）。
    """
    from tools import _transfer_schedule, _transfer_schedule_ops
    result = _transfer_schedule.schedule_transfer(confirmation.credential, otp)
    cycle = str(confirmation.filled.get("repeat_cycle") or "")
    if result.ok and cycle:
        schedule_id = str((result.data or {}).get("schedule_id") or "")
        if schedule_id:                                       # 周期记在同一笔上（T34）
            mark = _transfer_schedule_ops.set_schedule_repeat(
                schedule_id, cycle, str(confirmation.filled.get("repeat_until") or ""))
            if mark.ok:
                label = "每月" if cycle == "monthly" else "每周"
                result = result.model_copy(update={
                    "message": f"{result.message}（已设为{label}循环）"})
    return result
