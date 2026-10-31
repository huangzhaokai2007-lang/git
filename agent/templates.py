"""编排层回执模板（卡 09）：**数字一律靠占位符从 `ToolResult.facts` 注入**，模板里不写死任何业务数字。

铁律 1/2 的落点：
- 回执优先用这里的模板（`render`），LLM 只能给措辞润色、**不得改动任何数字**（编排层用校验器兜住）。
- 模板正文只有文字与占位符；占位符缺键会**直接报错**（宁可炸也不静默留白 —— 静默留白会变成"看起来对"的幻觉）。
- 反问/拒答/未接通/降级模板都不含任何业务数字（它们是"没有事实包"的路径）。

覆盖范围：卡 09 只接 8 个 L0 只读意图（`READ_INTENTS` 在 orchestrator 里定义，这里只管渲染）。
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from agent import llm
from agent._risk_questions import QUESTIONS as _RISK_QUESTIONS
#: 查询类回执（F02 流水明细 / F14 分类占比）：`render_query` 自己一份实现，这里再导出同一批对象。
from agent.render_query import (         # noqa: F401 —— 与 render_lists 同一拆法：import 即再导出
    BAR_CELLS, T_ANALYSIS_EMPTY, T_ANALYSIS_HEAD, T_ANALYSIS_NO_PREV,
    T_SPEND_TABLE_HEAD, T_SPEND_TABLE_SEP, T_TXN_EMPTY, T_TXN_HEAD,
    T_TXN_TABLE_HEAD, T_TXN_TABLE_SEP, TXN_ROWS, pct_bar, render_spending, render_txns,
    spend_row, txn_row,
)
from agent.render_lists import (          # noqa: F401 —— 列表类回执按职责拆出后**再导出**（同一份实现）
    CARD_STATUS_CN, CARD_TYPE_CN, CYCLE_LABELS, SEVERITY_CN, T_ANOMALY, T_CARD_HEAD,
    T_CARD_HEAD_FILTERED, T_CARD_NO_CARD,
    T_CARD_NO_CARD_FILTERED, T_PAYEE_EMPTY, T_PAYEE_HEAD, T_PAYEE_OTHER, T_PAYEE_WHITELIST,
    T_SUBSCRIPTION, T_SUBSCRIPTION_CLEAN, T_SUBSCRIPTION_EMPTY,
    T_SUBSCRIPTION_FILTERED, T_SUBSCRIPTION_ZOMBIE, T_WEALTH, T_WEALTH_CLEAN, T_WEALTH_EMPTY,
    TemplateError, anomaly_line, card_line, payee_row, render_all_products,
    render_aa_requests, render_anomalies, render_cards, render_gift_orders, render_holdings,
    render_payees,
    render_schedules,
    render_subscriptions, render_wealth, subscription_line, term_cn, wealth_line,
)
from guard import facts_check, injection
from tools.schemas import ToolResult

logger = logging.getLogger(__name__)


def sanitize_facts(value: object, *, key: str | None = None) -> object:
    """facts 的"送模型副本"：自由文本字段先包成 `<untrusted_data>`（铁律 7，卡 12b 收口）。

    不可信文本当**数据**不当指令 —— memo / counterparty / 备注 / IM 正文这类字段（含嵌在
    items[] 里的）会被包进数据块；数字与结构原样保留，润色的数字校验照旧可用。
    """
    if isinstance(value, dict):
        return {item: sanitize_facts(inner, key=item) for item, inner in value.items()}
    if isinstance(value, list):
        return [sanitize_facts(inner, key=key) for inner in value]
    if isinstance(value, str) and key in injection.FREE_TEXT_FIELDS:
        return injection.wrap_untrusted(f"facts.{key}", value)
    return value

# ---------------- 8 个只读意图的回执模板（占位符全部来自 facts） ----------------
#: 余额：facts 键见 tools/query.py T1（balance_yuan / available_yuan / as_of；account_type_cn 由编排层按
#: **非数字**标签注入，校验器只认数字，标签不影响校验）
T_BALANCE = "您的{account_type_cn}账户余额为 {balance_yuan} 元，可用余额 {available_yuan} 元（截至 {as_of}）。"

#: 流水：facts 键见 T2（total_count / page_out_sum_yuan / page_in_sum_yuan / items[] / date_from / date_to）
#: 实际渲染在 `render_query.render_txns`（头句带**查询区间** + **明细表**）；这里保留键位是为了
#: 「每个只读意图都有模板」这类检查不落空（与 T_ANOMALY 同口径）。
T_TXN = ("{date_from} 至 {date_to} 共查到 {total_count} 笔交易，"
         "其中支出合计 {page_out_sum_yuan} 元、收入合计 {page_in_sum_yuan} 元。")

#: 账单分析：facts 键见 T3（period / total_yuan / vs_prev_pct / groups[]）
#: 实际渲染在 `render_query.render_spending`（头句 + **分类占比表**）；同样只保留键位。
T_ANALYSIS = "{period} 共支出 {total_yuan} 元，环比 {vs_prev_pct}%。"

#: 异常检测：facts 键见 T4（scanned_count / anomaly_count / items[]）。
#: 实际渲染在 `render_lists.render_anomalies`（命中项要显示"交易 + 依据"，F03 验收第 2 条）；
#: 这里保留键位是为了"每个只读意图都有模板"这类检查不落空（与 T_CARD_HEAD 同口径）。
#: T_ANOMALY 由 render_lists 再导出（同一份实现）。

#: 账单报告：facts 键见 T5（period / out_sum_yuan / in_sum_yuan / net_yuan；markdown 由编排层附加）
T_REPORT_HEAD = "{period} 账单：支出 {out_sum_yuan} 元、收入 {in_sum_yuan} 元，净支出 {net_yuan} 元。"

#: 风险测评结果（F10）：facts 键见 T13（risk_level / total_score / max_score / valid_until）。
#: 回执**必须报出总分与满分**（验收清单第 1 条要求"由代码计算"，把总分亮出来才可核验），
#: 并说清**结果只存本次会话**（验收清单第 3 条：当前仅存进程内）。
T_RISK_RESULT = ("根据问卷计分（{total_score} 分），您的风险承受能力为「{risk_label}」，"
                 "有效期至 {valid_until}。（测评结果仅保存在本次会话中）")
#: 数字校验未通过时的降级措辞（不含任何数字；口径同 `T_DEGRADED_NOTE`）。
T_RISK_RESULT_FALLBACK = "您的风险测评已完成，具体等级与有效期请看测评结果。（测评结果仅保存在本次会话中）"
#: 问卷引导语（聊天触发时回，**不计分**；界面见到 `intent=risk_assess` 就弹问卷）。
T_RISK_PROMPT = (f"好的，风险测评一共 {len(_RISK_QUESTIONS)} 题，请在下面的问卷里作答，"
                 "提交后我立刻按您的回答算出风险等级。")

#: 反问（缺槽/低置信度）：只列缺失项的中文名，不含任何业务数字
T_CLARIFY_SLOT = "我需要再确认一下：请补充{slots_cn}。"
#: 异常分析缺时间段：**必须问清范围再答**（2026-10-27 验收口径：不许默认本月就说"检测到 N 笔"）
T_CLARIFY_ANOMALY_PERIOD = ("请告诉我您想查哪段时间的异常 —— 不同时间段结论不同，"
                            "我不会替您默认某个月。可以说「四月份」「上个月」「2026 年」或具体区间。")
T_CLARIFY_LOW_CONFIDENCE = "没太理解您的意思，能再说得具体一点吗？（例如「查一下余额」「上个月花了多少」）"
T_CLARIFY_TOO_MANY = "暂时没能确认您的需求。可以直接说“查余额”或“给谁转多少”；如需人工帮助，请联系银行客服。"

#: 拒答（unsafe_request）：模板化，不作任何工具调用
T_REFUSE = "这个请求我没法执行：{unsafe_reason}。我只能办理账户查询、账单分析等正常银行业务。"

#: 未接通（写操作类意图、或该意图暂时没有对应工具的意图 —— 如 `card_report_lost` 这类写卡操作；
#: 只读的 `card_query` 已由卡 23 接通）
T_UNSUPPORTED = "「{intent_cn}」这个功能还没接通，本版本暂时只支持查询类操作。"

#: 工具失败：只报错误码与工具自己的解释（工具消息里的数字本就来自 facts），不润色、不补数字
T_TOOL_ERROR = "这次没能查到您要的信息（{error_code}）：{detail}"
#: **业务拒绝**（黑名单 / 状态不允许 / 越权）不是「查不到」，措辞必须分开 ——
#: 实测缺陷：黑名单送礼被回成「这次没能查到您要的信息（FORBIDDEN）」，用户会以为系统坏了。
T_TOOL_REFUSED = "这笔操作不能办理（{error_code}）：{detail}"
_REFUSED_CODES = frozenset({"FORBIDDEN", "INVALID_STATE", "INSUFFICIENT_FUNDS"})

#: 数字校验未通过时的降级提示（与卡 13 的口径一致：降级为模板回执 + audit 记 HALLUCINATION_BLOCKED）
T_DEGRADED_NOTE = "（以下为系统直接给出的结果）"

#: 意图中文名（用于反问/未接通的措辞；纯文案，不含数字）
INTENT_CN: dict[str, str] = {
    "balance_query": "余额查询", "txn_query": "交易流水查询", "bill_analysis": "账单分析",
    "anomaly_check": "异常交易检测", "bill_report": "账单报告", "subscription_list": "订阅查询",
    "card_query": "卡片查询", "wealth_recommend": "理财推荐", "transfer_single": "转账",
    "transfer_scheduled": "预约转账", "aa_collect": "群收款", "subscription_cancel": "取消订阅",
    "subscription_remind": "订阅提醒", "card_apply": "申请新卡", "card_limit_adjust": "调整限额",
    "card_lock": "锁卡", "card_unlock": "解锁", "card_report_lost": "挂失", "risk_assess": "风险测评",
    "wealth_buy": "理财申购", "wealth_redeem": "理财赎回", "gift_plan": "送礼计划",
    "smalltalk": "闲聊", "out_of_scope": "越界请求", "unsafe_request": "不安全请求",
}

#: 槽位中文名（反问时用；**不做任何单位换算** —— 金额单位口径留 card-10 拍板）
SLOT_CN: dict[str, str] = {
    "account_type": "账户类型（储蓄/信用）", "date_from": "起始日期", "date_to": "结束日期",
    "period": "时间范围（如 2026-09 或 2026）", "category": "消费类别", "min_amount": "最低金额",
    "limit": "返回条数", "group_by": "分组方式", "kind": "报告类型", "status": "订阅状态",
    "card_id": "卡号", "risk_level": "风险等级", "horizon_days": "投资期限（天）", "amount": "金额",
    "product_id": "产品", "answers": "问卷答案", "contact": "联系人", "date": "日期", "budget": "预算",
    "payee": "收款人", "payee_ids": "收款人列表", "schedule": "预约时间", "split_with": "分账对象",
    "merchant": "订阅商户名", "sub_id": "订阅编号", "card_type": "卡片类型",
    #: F10 风险测评的五个题号短名（`risk_flow.QUESTION_KEYS_CN`）—— 部分作答时追问用
    "投资经验": "投资经验（如「1-3 年」）", "收入稳定性": "收入稳定程度",
    "可承受亏损": "可承受的最大亏损（如「10% 以内」）", "投资期限": "计划投资期限",
    "年龄": "年龄",
}

TEMPLATES: dict[str, str] = {
    "balance_query": T_BALANCE, "txn_query": T_TXN, "bill_analysis": T_ANALYSIS,
    "anomaly_check": T_ANOMALY, "bill_report": T_REPORT_HEAD, "subscription_list": T_SUBSCRIPTION,
    # 「每个只读意图都有模板」的键位不落空；card_query 实际按 total_count/status 分支（`render_cards`）
    "card_query": T_CARD_HEAD,
    "wealth_recommend": T_WEALTH,
}


from agent._render_remind import render_reminders


def render(intent: str, facts: Mapping[str, object]) -> str:
    """按意图渲染模板：所有数字来自 `facts`（缺键直接报错，不兜底、不猜）。"""
    if intent == "anomaly_check":                            # F03：汇总 + 每条命中一行（交易 + 依据）
        return render_anomalies(facts)
    if intent == "txn_query":                                # F02：头句 + 流水明细表（不再只报个合计）
        return render_txns(facts)
    if intent == "bill_analysis":                             # F14：头句 + 分类占比表（钱花在哪要看得见）
        return render_spending(facts)
    if intent == "card_query":                               # 卡 23：列表 + 空结果分支（不走单行 format）
        return render_cards(facts)
    if intent == "subscription_list":                        # 卡 20 + F19：汇总 + 明细，按状态分支
        return render_subscriptions(facts)
    if intent == "wealth_recommend":                         # F11：汇总 + 产品明细，空结果单独一句
        return render_wealth(facts)
    if intent == "payee_list":                               # SPEC-CHANGE：白名单 / 非白名单分组
        return render_payees(facts)
    if intent == "subscription_remind":                      # F18：N 天内要扣费的订阅
        return render_reminders(facts)
    if intent == "wealth_holdings":                          # T21：持仓明细
        return render_holdings(facts)
    if intent == "wealth_all_products":                      # T22：全部产品，按等级分组
        return render_all_products(facts)
    if intent == "scheduled_list":                           # F16 追加：预约列表（时间/收款人/金额/状态）
        return render_schedules(facts)
    if intent == "gift_order_list":                          # F20 追加：送礼订单（号码/收货人/金额/状态）
        return render_gift_orders(facts)
    if intent == "aa_list":                                  # F22 追加：群收款（已付人数/状态/截止）
        return render_aa_requests(facts)
    if intent == "risk_assess":                              # F10：总分/满分/等级/有效期（不走单行 format）
        try:
            return T_RISK_RESULT.format(**facts)
        except KeyError as exc:
            raise TemplateError(f"'risk_assess' 模板缺少 facts 键：{exc}") from exc
    template = TEMPLATES.get(intent)
    if template is None:
        raise TemplateError(f"没有为 {intent!r} 定义回执模板")
    try:
        return template.format(**facts)
    except KeyError as exc:                                  # 缺占位符：这是代码 bug，必须暴露
        raise TemplateError(f"{intent!r} 模板缺少 facts 键：{exc}") from exc


def clarify_missing(missing: list[str], intent: str = "") -> str:
    """缺槽反问：把缺失的槽位名换成人话（无业务数字）。

    `anomaly_check` 只说「请补充时间范围（如 2026-09 或 2026）」时用户很难照着答 ——
    异常分析的结论**随范围变**（这个问题本身就是这么暴露出来的），所以给一句能照着答的
    示例（「四月份」「上个月」「2026 年」），并把"为什么必须说"讲明白。
    """
    if intent == "anomaly_check" and missing == ["period"]:
        return T_CLARIFY_ANOMALY_PERIOD
    names = "、".join(SLOT_CN.get(slot, slot) for slot in missing)
    return T_CLARIFY_SLOT.format(slots_cn=names)


def clarify_low_confidence() -> str:
    return T_CLARIFY_LOW_CONFIDENCE


def clarify_to_human() -> str:
    return T_CLARIFY_TOO_MANY


def refuse(unsafe_reason: str | None) -> str:
    return T_REFUSE.format(unsafe_reason=(unsafe_reason or "该操作不在允许范围内").strip())


def unsupported(intent: str) -> str:
    return T_UNSUPPORTED.format(intent_cn=INTENT_CN.get(intent, intent))


#: 未测评时的引导（用户需求：直接问「要不要现在做」）。只对这一种 INVALID_STATE 生效。
T_NEED_RISK = ("您还没有完成风险测评，暂时不能买理财。\n\n"
               "**要不要现在做一下？** 一共 9 道题、大约 1 分钟，做完就能直接买。\n"
               "回复「**帮我做风险测评**」，我马上把问卷发您。")


def tool_error(error_code: str | None, detail: str) -> str:
    if error_code == "OVER_LIMIT" and detail.startswith("超过单笔转账上限"):
        return "超过单笔转账上限"
    if error_code == "INVALID_STATE" and ("风险测评" in detail or "风险评估" in detail):
        return T_NEED_RISK
    if error_code in _REFUSED_CODES:                     # 业务拒绝：换成「不能办理」
        return T_TOOL_REFUSED.format(error_code=error_code, detail=detail)
    return T_TOOL_ERROR.format(error_code=error_code or "ERROR", detail=detail)


def account_type_cn(account_type: object) -> str:
    """账户类型中文（模板占位符用；未知值原样返回，不猜）。"""
    return {"savings": "储蓄", "credit": "信用"}.get(str(account_type), str(account_type))


# ---------------- 回执生成：模板 → LLM 润色 → 数字校验（规格 §4 的 VERIFY_NUMBERS 判据） ----------------
POLISH_ATTEMPTS = 2           # 来源：卡 09 第 5 条 + 卡 13 口径「重生成一次 → 仍不过则降级为模板」
#: **结构型回执**（Markdown 表格）不进润色：润色器只被授权改「措辞」，但排版也是结构的一部分，
#: 数字校验器只核数字、**拦不住表格被压平成一串文字** —— 这类意图直接把模板原文交付。
#: 目前是 `payee_list`（T20「查看白名单」的表格）+ **本次登记的三个查询类**：
#: - `txn_query`（F02 流水明细表）、`bill_analysis`（F14 分类占比表）——本轮刚改成表格；
#: - `bill_report`（F24 账单报告）——回执主体是工具层生成的整篇 Markdown 文档，
#:   标题层级与表格都是结构，交给润色有可能被压平成一串文字。
#: 列表类（刷卡/订阅/理财）口径不变，以免顺手改掉别人的回执行为。
NO_POLISH_INTENTS = frozenset({"payee_list", "txn_query", "bill_analysis", "bill_report",
                               "wealth_recommend", "wealth_all_products"})
_TEMPLATE_ONLY = ContextVar('bank_template_only', default=False)


@contextmanager
def template_only():
    """Use fact-backed templates for this read context and restore on exit."""
    token = _TEMPLATE_ONLY.set(True)
    try:
        yield
    finally:
        _TEMPLATE_ONLY.reset(token)


def verify_numbers(text: str, facts: Mapping) -> set[str]:
    """返回回执里**未在 facts 出现**的数字集合（空集 = 通过）。

    判据的唯一实现已搬到 `guard/facts_check.verify_numbers`（规格 §7 的冻结签名 `-> (bool, list[str])`）；
    这里保留"未通过数字集合"的形态给模板层与编排层使用，语义与卡 09 完全一致。
    """
    passed, offenders = facts_check.verify_numbers(text, facts)
    return set() if passed else set(offenders)


class _Polished(BaseModel):
    """润色输出（最小 schema：只允许一个字段，多余字段即校验失败）。"""

    model_config = ConfigDict(extra="forbid")

    reply: str


POLISH_SYSTEM = (
    "你是银行智能体的措辞润色器。输入是一段已经生成好的回执与它的事实包。"
    "任务：只调整措辞让它更自然，**严禁改动、删除、新增任何数字**（金额、笔数、百分比、日期都不许动），"
    '也不许添加事实包里没有的信息。只输出 JSON：{"reply": "润色后的文本"}。'
)


def polish(text: str, facts: Mapping) -> str | None:
    """让 LLM 润色措辞；LLM 不可用或返回空 → `None`（**读请求不因润色失败而失败**）。"""
    payload = json.dumps({"reply": text, "facts": sanitize_facts(facts)}, ensure_ascii=False,
                         default=str)                                  # 自由文本先包成数据块（铁律 7）
    try:
        out = llm.chat_json(POLISH_SYSTEM, payload, _Polished)
    except llm.LLMUnavailable:
        logger.warning("润色不可用（LLM 不可用），改用模板原样回执")
        return None
    reply = getattr(out, 'reply', None)
    return (reply.strip() or None) if isinstance(reply, str) else None


def _template_values(intent: str, slots: Mapping, result: ToolResult) -> dict:
    """模板占位符取值：**数字只从 facts 来**，编排层只补非数字标签（如账户类型中文名）。"""
    values: dict = dict(result.facts)
    if intent == "balance_query":
        values["account_type_cn"] = account_type_cn(slots.get("account_type") or "savings")
    if intent == "card_query":                                # 卡 23：状态过滤的措辞标签（表外值原样透传）
        status = str(slots.get("status") or "")
        values["status_cn"] = CARD_STATUS_CN.get(status, status) if status else ""
    return values


def compose_reply(intent: str, slots: Mapping, result: ToolResult) -> tuple[str, bool, int]:
    """模板 → LLM 润色 → 数字校验。返回 `(回执, 是否因幻觉降级, 润色尝试次数)`。

    降级条件（卡 13 口径）：润色结果出现 facts 之外的数字 → 重生成一次 → 仍不过 → 回模板原样文本。
    `NO_POLISH_INTENTS` 里的结构型回执（表格）跳过润色，直接交付模板原文。
    """
    reply = render(intent, _template_values(intent, slots, result))
    if intent == "bill_report" and result.data.get("markdown"):
        reply = f"{reply}\n\n{result.data['markdown']}"
    if intent in NO_POLISH_INTENTS or _TEMPLATE_ONLY.get():    # 结构化或后台回执保持事实模板
        return reply, False, 0
    hallucinated, attempts = False, 0
    for _ in range(POLISH_ATTEMPTS):
        attempts += 1
        candidate = polish(reply, result.facts)
        if candidate is None:
            break
        if not verify_numbers(candidate, result.facts):
            return candidate, False, attempts
        hallucinated = True
        logger.warning("润色后出现 facts 之外的数字，第 %s 次重生成：intent=%s", attempts, intent)
    return reply, hallucinated, attempts
