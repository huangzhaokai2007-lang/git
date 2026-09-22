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
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from agent import llm
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

#: 流水：facts 键见 T2（total_count / page_out_sum_yuan / page_in_sum_yuan / items[]）
#: 注意：T2 的 facts **没有** date_from/date_to 键 → 模板不得印时间范围（否则数字校验会把它当幻觉）
T_TXN = "共查到 {total_count} 笔交易，其中支出合计 {page_out_sum_yuan} 元、收入合计 {page_in_sum_yuan} 元。"

#: 账单分析：facts 键见 T3（period / total_yuan / vs_prev_pct / groups[]）
T_ANALYSIS = "{period} 共支出 {total_yuan} 元，环比 {vs_prev_pct}%。"

#: 异常检测：facts 键见 T4（scanned_count / anomaly_count / items[]）
T_ANOMALY = "已扫描 {scanned_count} 笔交易，检测到 {anomaly_count} 笔异常，请留意。"

#: 账单报告：facts 键见 T5（period / out_sum_yuan / in_sum_yuan / net_yuan；markdown 由编排层附加）
T_REPORT_HEAD = "{period} 账单：支出 {out_sum_yuan} 元、收入 {in_sum_yuan} 元，净支出 {net_yuan} 元。"

#: 订阅列表：facts 键见 T10（subscription_count / zombie_count / zombie_window_months / zombie_as_of）
#: 卡 20 追加（人类口径）：面向用户**不说行话** —— 回执里不再出现"僵尸"二字，改成
#: "仍在进行中、但最近 W 个月没有任何扣费记录"。`zombie_*` 只留在代码内部命名与 facts 键里。
T_SUBSCRIPTION_CLEAN = "当前有 {subscription_count} 个订阅，最近 {zombie_window_months} 个月都有正常扣费记录。"
T_SUBSCRIPTION_ZOMBIE = ("当前有 {subscription_count} 个订阅；其中 {zombie_count} 个仍在「进行中」，"
                         "但最近 {zombie_window_months} 个月没有任何扣费记录 —— 怀疑是您忘了取消的订阅，"
                         "建议核对一下。")
#: `TEMPLATES` 里的默认值（实际按 `zombie_count` 分支，见 `render`）；
#: 保留这个键位是为了"每个只读意图都有模板"这类检查不落空。
T_SUBSCRIPTION = T_SUBSCRIPTION_ZOMBIE

#: 理财推荐：facts 键见 T14（risk_level / item_count）
T_WEALTH = "按风险等级 {risk_level} 为您推荐 {item_count} 个产品。"

#: 卡查询（卡 23）：facts 键见 `tools/card_query.py`（`total_count` + `items[]`；每项含 `card_no_mask`
#: 与三档额度的分/元两份）。空结果有自己的句子（绝不渲染成「共 0 张：」这种残句）；
#: `status_cn` 由编排层按槽位补的**非数字**标签（照 `account_type_cn` 的先例），有它就走带过滤的措辞。
T_CARD_HEAD = "您名下共有 {total_count} 张卡："
T_CARD_HEAD_FILTERED = "您名下有 {total_count} 张{status_cn}的卡："
T_CARD_NO_CARD = "您名下目前没有卡片。"
T_CARD_NO_CARD_FILTERED = "您名下没有{status_cn}的卡。"

#: 卡片状态 / 类型的中文：面向用户**只说大白话**（`lost` 说「已挂失」、`frozen` 说「已冻结」），
#: 内部枚举名一个字都不出现在回执里；表外的值原样透传（不猜、不编）。
CARD_STATUS_CN = {"normal": "正常", "locked": "已锁定", "lost": "已挂失", "frozen": "已冻结"}
CARD_TYPE_CN = {"savings": "储蓄卡", "credit": "信用卡"}

#: 反问（缺槽/低置信度）：只列缺失项的中文名，不含任何业务数字
T_CLARIFY_SLOT = "我需要再确认一下：请补充{slots_cn}。"
T_CLARIFY_LOW_CONFIDENCE = "没太理解您的意思，能再说得具体一点吗？（例如「查一下余额」「上个月花了多少」）"
T_CLARIFY_TOO_MANY = "连续两次没能确认您的需求，我先转人工客服帮您处理。"

#: 拒答（unsafe_request）：模板化，不作任何工具调用
T_REFUSE = "这个请求我没法执行：{unsafe_reason}。我只能办理账户查询、账单分析等正常银行业务。"

#: 未接通（写操作类意图、或该意图暂时没有对应工具的意图 —— 如 `card_report_lost` 这类写卡操作；
#: 只读的 `card_query` 已由卡 23 接通）
T_UNSUPPORTED = "「{intent_cn}」这个功能还没接通，本版本暂时只支持查询类操作。"

#: 工具失败：只报错误码与工具自己的解释（工具消息里的数字本就来自 facts），不润色、不补数字
T_TOOL_ERROR = "这次没能查到您要的信息（{error_code}）：{detail}"

#: 数字校验未通过时的降级提示（与卡 13 的口径一致：降级为模板回执 + audit 记 HALLUCINATION_BLOCKED）
T_DEGRADED_NOTE = "（以下为系统直接给出的结果）"

#: 意图中文名（用于反问/未接通的措辞；纯文案，不含数字）
INTENT_CN: dict[str, str] = {
    "balance_query": "余额查询", "txn_query": "交易流水查询", "bill_analysis": "账单分析",
    "anomaly_check": "异常交易检测", "bill_report": "账单报告", "subscription_list": "订阅查询",
    "card_query": "卡片查询", "wealth_recommend": "理财推荐", "transfer_single": "转账",
    "transfer_scheduled": "预约转账", "aa_collect": "AA 收款", "subscription_cancel": "取消订阅",
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
    "sub_id": "订阅", "card_type": "卡片类型",
}

TEMPLATES: dict[str, str] = {
    "balance_query": T_BALANCE, "txn_query": T_TXN, "bill_analysis": T_ANALYSIS,
    "anomaly_check": T_ANOMALY, "bill_report": T_REPORT_HEAD, "subscription_list": T_SUBSCRIPTION,
    # 「每个只读意图都有模板」的键位不落空；card_query 实际按 total_count/status 分支（`render_cards`）
    "card_query": T_CARD_HEAD,
    "wealth_recommend": T_WEALTH,
}


class TemplateError(RuntimeError):
    """模板渲染失败（缺占位符 / 键名写错）。**宁可炸**也不许静默留白（静默留白=幻觉的温床）。"""


def card_line(item: Mapping) -> str:
    """一张卡一行（`- ` 开头：界面用 `st.markdown` 渲染回执，会排成项目符号列表；卡号照抄、不拼接、不补全）。

    数字全部照抄 facts 对应项，不做任何换算：卡号里的数字来自 `card_no_mask` 本体，
    额度来自 `*_yuan` 展示串。储蓄卡没有授信额度（facts 里 `credit_limit_yuan` 是 `None`）→ 不印那一截。
    """
    kind = CARD_TYPE_CN.get(str(item["type"]), item["type"])
    state = CARD_STATUS_CN.get(str(item["status"]), item["status"])
    line = f"- {item['card_no_mask']}（{kind} · {state}）"
    if item.get("credit_limit_yuan"):
        line += f"，额度 {item['credit_limit_yuan']} 元"
    return line


def render_cards(facts: Mapping) -> str:
    """`card_query` 回执：按 `total_count` / `status_cn` 分支（卡 23）。

    - 无卡（或该状态一张都没有）→ **自己的句子**，不套列表头（避免「共 0 张：」这种残句）；
    - 有卡 → 列表头 + 每张一行（数字全部来自 facts）。
    """
    count = int(facts["total_count"])
    status_cn = str(facts.get("status_cn") or "")
    if count == 0:
        return T_CARD_NO_CARD_FILTERED.format(status_cn=status_cn) if status_cn else T_CARD_NO_CARD
    head = (T_CARD_HEAD_FILTERED.format(total_count=count, status_cn=status_cn) if status_cn
            else T_CARD_HEAD.format(total_count=count))
    return "\n".join([head, *(card_line(item) for item in facts["items"])])


def render(intent: str, facts: Mapping[str, object]) -> str:
    """按意图渲染模板：所有数字来自 `facts`（缺键直接报错，不兜底、不猜）。"""
    if intent == "card_query":                               # 卡 23：列表 + 空结果分支（不走单行 format）
        return render_cards(facts)
    if intent == "subscription_list":                        # 卡 20：按有没有"忘了取消"的订阅分两种说法
        chosen = T_SUBSCRIPTION_ZOMBIE if facts.get("zombie_count") else T_SUBSCRIPTION_CLEAN
        try:
            return chosen.format(**facts)
        except KeyError as exc:
            raise TemplateError(f"{intent!r} 模板缺少 facts 键：{exc}") from exc
    template = TEMPLATES.get(intent)
    if template is None:
        raise TemplateError(f"没有为 {intent!r} 定义回执模板")
    try:
        return template.format(**facts)
    except KeyError as exc:                                  # 缺占位符：这是代码 bug，必须暴露
        raise TemplateError(f"{intent!r} 模板缺少 facts 键：{exc}") from exc


def clarify_missing(missing: list[str]) -> str:
    """缺槽反问：把缺失的槽位名换成人话（无业务数字）。"""
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


def tool_error(error_code: str | None, detail: str) -> str:
    return T_TOOL_ERROR.format(error_code=error_code or "ERROR", detail=detail)


def account_type_cn(account_type: object) -> str:
    """账户类型中文（模板占位符用；未知值原样返回，不猜）。"""
    return {"savings": "储蓄", "credit": "信用"}.get(str(account_type), str(account_type))


# ---------------- 回执生成：模板 → LLM 润色 → 数字校验（规格 §4 的 VERIFY_NUMBERS 判据） ----------------
POLISH_ATTEMPTS = 2           # 来源：卡 09 第 5 条 + 卡 13 口径「重生成一次 → 仍不过则降级为模板」
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
    return out.reply.strip() or None


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
    """
    reply = render(intent, _template_values(intent, slots, result))
    if intent == "bill_report" and result.data.get("markdown"):
        reply = f"{reply}\n\n{result.data['markdown']}"
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
