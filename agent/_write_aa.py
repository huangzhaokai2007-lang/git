"""群收款族写描述符（T9）：**无凭证族**，L1，不落资金。

F15 卡新增（`agent/_write_common.py` 是核心契约类型，`agent/write_intents.py` 是注册表）。

**口径逐条对齐 F15 验收清单**：

| F15 验收点 | 本模块怎么满足 |
|---|---|
| 联系人去重且归属当前用户 | `_shape_aa` 逐个走 T6 `resolve_payee`（**owner 圈定**）→ 按 id 去重 |
| **确认前不发起请求** | `prepare` **不调** `create_aa_request`（那会写审计 + 生成 `request_id`），只用 `tools._aa_split` 纯算 |
| 分摊和余数规则可解释 | 与执行回执共用 `tools._aa_split` 的**同一份实现**，不自己再算 |
| 只说「发起请求」不说「对方已付款」 | `_card_aa` / `_plain_aa` 的措辞都写「发起 群收款」 |
| 用户可见数字来自工具事实包 | 卡面数字全部取自 `facts`，`write_flow` 的 `card_numbers_outside_facts` 守门 |

**两种缺口如实登记**：
- **无凭证 ⇒ 无工具层幂等**：T9 签名没有 `preview_token`/`confirm_ref`，重复保护只来自
  `write_flow._execute` 成功后的 `confirm_card.clear(session_id)` 一次性消费（同 T16 口径）。
- **不落资金**：T9 只写一条审计，不动 `balance`/`available` —— 故措辞严禁出现「已收款」。
"""

from __future__ import annotations

import re
from typing import Mapping

from agent import confirm_card, payee_clarify, payee_resolve, payee_risk
from agent._write_common import Prepared, ShapeResult, normalize_query, yuan_to_cents
from tools import _aa_split, transfer
from tools.schemas import ToolResult

AA_CN = "群收款"

#: 群收款要不要因为「含非白名单收款人」而要验证码？
#: - **False（当前口径，2026-10-25）**：AA 是**收款**方向，一律免码；黑名单照旧直接拒。
#: - True：恢复到 2026-10-24 的旧规则（含非白名单 / 白名单已过期 → L2 要码）。
AA_REQUIRE_OTP_FOR_NON_WHITELIST = False

#: 「王五 100 元、赵六 200 元」→ (名字, 元数) 的确定性解析（不赌 LLM）
_AMOUNT_PAIR = re.compile(r"^(.+?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?$")


def _as_list(raw: object) -> list[str]:
    """槽位容忍三种形态：列表 / 单个字符串 / 空 —— 分类器给的 `payee_ids` 未必规范。"""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    return [text] if text else []


def _parse_per_person(raw: object, picked: dict[str, str],
                      aliases: dict[str, str] | None = None) -> tuple[list[int] | None, str | None]:
    """「李四 30、妈妈 40、房东 50」→ 按 `picked` 顺序的**整数分**列表；解析不了 → `(None, 原因)`。

    名字既可以是**真名**（解析后的姓名），也可以是**用户原话**（标签「房东」/ 手机号「139****1001」）——
    实测：用户说「向李四，妈妈，房东…各 30,40,50」，而解析后的名单里是「李四、张小美、王五」，
    只按真名比对会把「妈妈/房东」判成「不在名单里」。`aliases` 就是「原话 → payee_id」的映射。
    金额用 `yuan_to_cents`（与转账同一份解析，禁浮点）。
    """
    text = str(raw or "").strip()
    if not text:
        return None, None
    by_name = {name: payee_id for payee_id, name in picked.items()}
    cents_of: dict[str, int] = {}
    for seg in re.split(r"[、,，;；]", text):
        seg = seg.strip()
        if not seg:
            continue
        match = _AMOUNT_PAIR.match(seg)
        if match is None:
            return None, f"「{seg}」看不懂，请写成「王五 100 元、赵六 200 元」的样子"
        name = match.group(1).strip()
        #: 2026-10-30：卡面按 id 选人，按人金额这里也可能是 `payee_0003 1500` —— 
        #: 直接认 id（同名两位也不会串），再退回姓名/原话别名。
        payee_id = (name if name in picked else None) or by_name.get(name) \
            or (aliases or {}).get(name)
        if payee_id is None or payee_id not in picked:
            return None, f"「{name}」不在这次群收款的名单里"
        cents = yuan_to_cents(match.group(2))
        if cents is None:
            return None, f"「{seg}」的金额看不懂"
        cents_of[payee_id] = cents
    lacking = [name for payee_id, name in picked.items() if payee_id not in cents_of]
    if lacking:
        return None, f"还缺 {'、'.join(lacking)} 的金额"
    return [cents_of[payee_id] for payee_id in picked], None


def _shape_aa(slots: Mapping) -> ShapeResult:
    """SLOT_FILL：`payee_ids`（名单，可为姓名/手机号/标签）→ **去重后**的 id 列表；`amount` → 整数分。

    逐个走统一解析关卡（标签 / 同音 / 黑名单一套口径）：零命中或同名多个 → 记缺槽并列出
    脱敏候选，**绝不擅自选一个**；黑名单命中 → 整笔 AA 直接拒（人类需求：黑名单不能收款）。

    可选 `amounts` 槽位（「王五 100 元、赵六 200 元」）：按人金额；没报总额时总额 = 合计；
    报了总额且与合计不符 → 直接报错。
    """
    filled, missing = dict(slots), []
    picked: dict[str, str] = {}                     # id → name，顺带完成去重（T9 对重复收款人直接拒）
    aliases: dict[str, str] = {}                    # 用户原话（标签/手机号）→ id，供按人金额认名字
    clarify, unresolved = "", False
    candidates: list[dict] = []                     # 同名候选 → 交给解析卡
    for query in _as_list(filled.get("payee_ids")):
        raw = str(query).strip()
        #: 2026-10-30：卡面传的是 **payee_id**（同名两位也能区分）；按 id 直接取画像，
        #: 不再拿名字走一遍歧义解析。
        by_id = payee_resolve.profile_of(raw) if raw.startswith("payee_") else None
        if by_id:
            picked.setdefault(raw, str(by_id.get("name") or raw))
            aliases.setdefault(raw, raw)
            continue
        person, code, facts = payee_resolve.resolve_or_explain(normalize_query(query))
        if code:                                        # 黑名单：整笔 AA 直接拒（不当候选）
            return ShapeResult(filled=filled, error_code=code, message=str(facts.get("message") or ""))
        if person is None:
            unresolved = True
            candidates.extend(facts.get("candidates") or [])   # 供界面出**解析卡**
            clarify = clarify or payee_clarify.ask_for(facts)
        else:
            picked.setdefault(person["payee_id"], person["name"])
            aliases.setdefault(str(query).strip(), person["payee_id"])
    if unresolved or not picked:
        missing.append("payee_ids")
    else:
        filled["payee_ids"] = list(picked)
        filled["aa_names"] = [picked[payee_id] for payee_id in picked]
        filled["aa_target"] = "、".join(filled["aa_names"])      # 审计与卡面用的可读主对象
    #: 2026-10-24 修：**名单没解析全时先不校验按人金额**。
    #: 实测「向李四，妈妈，房东，aa 收款各 30,40,50」——李四同名进解析卡待选，
    #: 但按人金额里写着「李四 30」，跟已解析出的名单一比就报「李四不在名单里」，
    #: 把用户挡在门外。正确顺序是：先解析出人（解析卡），再校验金额。
    if picked and not unresolved and filled.get("per_person_cents") is None and filled.get("amounts"):
        per_person, err = _parse_per_person(filled["amounts"], picked, aliases)
        if err is not None:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT", message=err)
        filled["per_person_cents"] = per_person
        if filled.get("amount") is None and filled.get("amount_cents") is None:
            filled["amount_cents"] = sum(per_person)             # 没报总额 → 按人金额的合计
    if filled.get("amount_cents") is None:
        cents = yuan_to_cents(filled.get("amount")) if filled.get("amount") is not None else None
        if cents is None and not filled.get("amounts"):      # 按人金额在手时不算「缺金额」
            missing.append("amount")
            # 同名与缺金额**同时发生**时两件事都要说（否则用户以为只差选人）
            #: 文案里带上 write_flow 的槽位标签原文（「金额」），否则它会再追加
            #: 「请补充：金额（元）」—— 用户看到同一件事被问两遍（实测）。
            amount_hint = "请补充金额（元），例如「群收款 300 元」。"
            clarify = f"{clarify}\n{amount_hint}" if clarify else amount_hint
        else:
            filled["amount_cents"] = cents
    if filled.get("per_person_cents") and filled.get("amount_cents") is not None:
        shares = sum(filled["per_person_cents"])
        #: 2026-10-30（含我口径）：Σ每人只要求 **不超过** 含我总额，
        #: 差额就是我承担的那一份（`amount_cents` 是含我总额，卡面上写的 300）。
        if shares > filled["amount_cents"]:
            return ShapeResult(filled=filled, error_code="INVALID_ARGUMENT",
                               message=f"每人金额合计 {shares / 100:.2f} 元，超过群收款总额 "
                                       f"{filled['amount_cents'] / 100:.2f} 元，请改一个")
        filled["my_share_cents"] = filled["amount_cents"] - shares
    return ShapeResult(filled=filled, missing=missing, clarify=clarify,
                       candidates=candidates)


def _prepare_aa(filled: dict) -> Prepared:
    """**只算不执行**：用 `tools._aa_split` 算事实包，**不调** `create_aa_request`。

    「确认前不发起请求」由此在编排层被结构性保证 —— 真发起只在 `execute` 里。
    AA 是**收款**方向（钱进来不是出去）→ L1、免验证码（人类需求：收款不用验证码）；
    黑名单拦截已在 `_shape_aa` 完成。
    """
    ids = list(filled.get("payee_ids") or [])
    names = list(filled.get("aa_names") or [])
    if not ids or len(names) != len(ids):
        return Prepared(ok=False, error_code="INVALID_ARGUMENT", message="收款人名单不完整，请重新提供")
    payees = [{"id": pid, "name": name} for pid, name in zip(ids, names)]
    facts = _aa_split.aa_facts(payees, int(filled["amount_cents"]),
                               per_person=filled.get("per_person_cents"))
    #: 2026-10-28（用户要求「群收款要包含我」）：一键均摊时，用户说的总额是**含我**的，
    #: 份数 = 人数 + 1（我占一份）→ 每人应缴、我承担、待收 三个数都来自工具层。
    if facts.get("split_mode") == "equal":
        plan = _aa_split.split_with_self(int(filled["amount_cents"]), len(payees))
        facts.update({
            "per_person_amount": plan["per_cents"],
            "per_person_amount_yuan": f"{plan['per_cents'] / 100:.2f}",
            "initiator_share": plan["mine_cents"],
            "initiator_share_yuan": f"{plan['mine_cents'] / 100:.2f}",
            "my_share_cents": plan["mine_cents"],
            "collect_cents": plan["collect_cents"],
            "collect_yuan": f"{plan['collect_cents'] / 100:.2f}",
            "per_person_cents": [plan["per_cents"]] * len(payees),
        })
    else:
        #: 2026-10-31 修（用户实测卡面全是「—」）：按人金额 / 按份数均摊时，
        #: 每人应缴、我承担、待收 也得由工具层的**同一份事实**给出来，卡面不能空。
        per = [int(value) for value in (filled.get("per_person_cents") or [])]
        if per:
            shares = sum(per)
            mine = int(filled["amount_cents"]) - shares
            facts.update({
                "per_person_cents": per,
                "collect_cents": shares,
                "collect_yuan": f"{shares / 100:.2f}",
                "my_share_cents": mine,
                "initiator_share": mine,
                "initiator_share_yuan": f"{mine / 100:.2f}",
                "per_person_amount_yuan": "、".join(f"{value / 100:.2f}" for value in per),
                "per_person_items": [
                    {"name": name, "cents": value, "yuan": f"{value / 100:.2f}"}
                    for name, value in zip(names, per)],
            })
    if "per_person_items" not in facts:
        #: 一键均摊：每人相同，也给出逐人明细（卡面统一按"逐人列出"渲染）
        unit = int(facts.get("per_person_amount") or 0)
        facts["per_person_items"] = [{"name": name, "cents": unit, "yuan": f"{unit / 100:.2f}"}
                                     for name in names]
    #: 2026-10-31（用户需求）：群收款要能选「收款到我哪张卡」——卡信息进事实包，
    #: 卡面只印这里的值（铁律 2）。
    from agent import my_card_flow
    my_card = my_card_flow.pick(filled.get("my_card_id"))
    if my_card:
        facts.update({"my_card_bank": my_card["bank"], "my_card_mask": my_card["card_no_mask"],
                      "my_card_type": my_card["card_type"], "my_card_id": my_card["card_id"]})
    facts["target_name"] = filled["aa_target"]
    #: 2026-10-24（人类规格「群收款总额同样受限额约束」）：超单笔上限 / 超今日剩余 → 直接拒。
    single_max = facts.get("limits_single_max")
    daily_left = facts.get("limits_daily_remaining")
    total_cents = int(facts.get("amount_cents") or filled.get("amount_cents") or 0)
    if single_max is not None and total_cents > int(single_max):
        return Prepared(ok=False, error_code="OVER_LIMIT",
                        message=f"群收款总额 {total_cents / 100:.2f} 元超过单笔上限 "
                                f"{int(single_max) / 100:.2f} 元")
    if daily_left is not None and total_cents > int(daily_left):
        return Prepared(ok=False, error_code="OVER_LIMIT",
                        message=f"群收款总额 {total_cents / 100:.2f} 元超过今日剩余额度 "
                                f"{int(daily_left) / 100:.2f} 元")
    #: 2026-10-25 口径（人类最新原话）：「群收款**不要**验证码」—— 收款方向，钱是进来的。
    #: 因此恒 L1；黑名单仍然在 `_shape_aa` 直接拒（那一条不受影响）。
    #: 想恢复 10-24 的旧规则（含非白名单收款人要码）：把下面这个常量改回 True 即可。
    non_exempt = [name for pid, name in zip(ids, names) if not _exempt(pid)]
    facts["tier"] = (payee_risk.TIER_L2 if (AA_REQUIRE_OTP_FOR_NON_WHITELIST and non_exempt)
                     else payee_risk.TIER_L1)
    if AA_REQUIRE_OTP_FOR_NON_WHITELIST and non_exempt:
        facts["aa_otp_reason"] = "含非白名单收款人：" + "、".join(non_exempt)
    return Prepared(ok=True, facts=facts, credential="")


def _exempt(payee_id: str) -> bool:
    """这个收款人在 AA 里免验证码吗：**白名单且未过期**（黑名单更早被拒，不走这里）。"""
    person = payee_resolve.profile_of(payee_id) or {}
    return bool(person.get("is_whitelist")) and not payee_risk.is_expired(person)


def _card_aa(intent: str, filled: dict, facts: Mapping, tier: str,
             requires_otp: bool) -> confirm_card.CardInput:
    """AA 确认卡：人数 / 总额 / 每人（一键均摊 或 按人明细）**全部来自 facts**。"""
    if facts.get("split_mode") == "custom":
        rows = [("收款人", f"{facts['target_name']}（{facts['payee_count']} 人）"),
                ("总金额", f"{facts['amount_yuan']} 元")]
        rows += [(f"　{line['name']}", f"{line['amount_yuan']} 元")
                 for line in facts["per_person_lines"]]
        rows.append(("分摊方式", "按人金额"))
    else:
        rows = [("收款人", f"{facts['target_name']}（{facts['payee_count']} 人）"),
                ("总金额（含我）", f"{facts['amount_yuan']} 元"),
                ("每人", f"{facts['per_person_amount_yuan']} 元（含我一份）"),
                ("我承担", f"{facts['initiator_share_yuan']} 元"),
                ("待收", f"{facts.get('collect_yuan', facts['amount_yuan'])} 元")]
    if facts.get("my_card_mask"):                       # 2026-10-31：钱收到我哪张卡
        card_type = str(facts.get("my_card_type") or "储蓄卡")
        card_bank = str(facts.get("my_card_bank") or "")
        card_mask = str(facts["my_card_mask"])
        rows.insert(1, ("收款到", f"{card_type} {card_bank} {card_mask}"))
    if facts.get("aa_otp_reason"):                      # 为什么要验证码，卡面说清楚
        rows.append(("验证码原因", str(facts["aa_otp_reason"])))
    return confirm_card.CardInput(
        title="群收款确认卡", intent_cn=AA_CN, rows=rows,
        tier=tier, requires_otp=requires_otp,
        risk_note=confirm_card.risk_note([], AA_CN))


def _plain_aa(filled: dict, facts: Mapping, tier: str) -> str:
    """纯事实版确认文本（AA 版降级回执）。"""
    if facts.get("split_mode") == "custom":
        detail = "，".join(f"{line['name']} {line['amount_yuan']} 元"
                           for line in facts["per_person_lines"])
        return (f"请确认：向 {facts.get('target_name')}（{facts.get('payee_count')} 人）"
                f"发起 群收款 {facts.get('amount_yuan')} 元（{detail}），权限档 {tier}。")
    return (f"请确认：向 {facts.get('target_name')}（{facts.get('payee_count')} 人）发起 群收款 "
            f"{facts.get('amount_yuan')} 元，每人 {facts.get('per_person_amount_yuan')} 元（一键均摊），"
            f"权限档 {tier}。")


def _execute_aa(confirmation: confirm_card.Confirmation, otp: str | None) -> ToolResult:
    """真发起：按人金额 → **T31**；一键均摊 → **T9**（签名冻结，按人金额走新工具）。"""
    del otp
    filled = confirmation.filled
    #: 含我口径的三个数在**事实包**里（`evidence`）：每人应缴 / 我承担 / 待收。
    evidence = dict(confirmation.evidence or {})
    payee_ids = list(filled.get("payee_ids") or [])
    per_person = list(evidence.get("per_person_cents")
                      or filled.get("per_person_cents") or [])
    mode = "custom" if per_person else "equal"
    #: 2026-10-29（用户要求「把你没写的写了」）：落库口径改成**含我总额** ——
    #: `aa_request.total` = 卡面上的 300，`aa_share` = 每人应缴 150；
    #: 「待收 / 我承担」由 T42 按差额算（total − Σ份额），不改表结构。
    group_total = int(confirmation.amount_cents or evidence.get("amount") or 0)
    if per_person:
        result = _aa_split.create_aa_request_with_amounts(payee_ids, per_person)
        amount_cents = per_person
        #: 含我总额以**确认卡上的金额**为准（evidence 里叫 amount，不叫 amount_cents）
        group_total = max(int(confirmation.amount_cents or 0), sum(per_person))
    else:
        #: T9 的口径是「向这些人收多少」→ 传**待收**（含我总额 − 我那份）。
        collect = int(evidence.get("collect_cents") or 0) or group_total
        result = transfer.create_aa_request(payee_ids, collect)
        per = collect // max(len(payee_ids), 1)
        amount_cents = [per] * len(payee_ids)
        if amount_cents:                       # 余数记到第一份（与 T9 的分摊口径一致）
            amount_cents[0] += collect - per * len(payee_ids)
        group_total = max(group_total, sum(amount_cents))     # 含我总额恒 ≥ 待收
    if result.ok:                              # T41：把 AA 变成可跟踪的请求（24h 有效）
        from tools import _aa_request
        opened = _aa_request.open_aa_request(payee_ids, amount_cents, group_total, mode,
                                              str(filled.get("title") or "群收款"),
                                              my_card_id=filled.get("my_card_id"))
        if opened.ok:
            result = result.model_copy(update={
                "message": f"{result.message}（群收款单号 {opened.data['request_id']}，24 小时内有效）"})
    return result
