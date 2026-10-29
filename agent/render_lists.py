"""编排层**列表类回执**渲染（从 `agent/templates.py` 拆出，卡 23/20 与 F19 的产物）。

为什么单开一个文件：`templates.py` 被 F19 的订阅明细推到 325 行（红线 300，CLAUDE.md 第 51 行），
本仓先例是「按职责拆出去、原处反向 import 并 re-export」——照 `agent/read_routes.py`（卡 23 拆
`TOOL_ROUTES`）与 `agent/period.py`（卡 17b 拆时间解析）的做法：
`templates.render_cards` / `templates.render_subscriptions` 仍是**同一份实现**（不是副本），
历史调用点与测试不受影响。

分层：本模块属编排层，只 import `tools/` 的纯文案常量与 `agent/` 自己人；不碰 `data/`、不写 SQL。
数字纪律（铁律 2）：这里的每一行明细都**照抄 facts**，不做换算、不做拼接、不补全。
"""

from __future__ import annotations

from typing import Mapping

from tools.subscription import (
    CYCLE_LABELS,                        # 周期文案的唯一来源：本模块 import、templates 再导出
    STATUS_LABELS as STATUS_CN,
)
#: 理财列表回执（推荐 / 全部产品）已拆到 `agent/render_wealth.py`（同一份实现，此处再导出，
#: 历史调用点与测试零改动）。RISK_LABELS 也由理财渲染独占使用，随拆随走。
from agent.render_wealth import (  # noqa: F401 —— 再导出：同一份实现
    T_WEALTH, T_WEALTH_CLEAN, T_WEALTH_EMPTY, RISK_LABELS,  # noqa: F401
    category_label, render_all_products, render_wealth, term_cn,  # noqa: F401
    wealth_groups, wealth_line,  # noqa: F401
)

__all__ = ["CARD_STATUS_CN", "CARD_TYPE_CN", "CYCLE_LABELS", "SEVERITY_CN", "TemplateError",
           "anomaly_line", "badge", "card_line", "payee_row", "render_all_products",
           "render_anomalies", "render_aa_requests", "render_cards", "render_gift_orders",
           "render_holdings", "render_payees", "render_schedules", "render_subscriptions",
           "render_wealth", "subscription_line", "wealth_line"]

from agent.render_tables import (
    TemplateError,  # noqa: F401 —— 再导出（唯一定义在 render_tables）    # noqa: F401 —— 再导出：同一份实现，历史调用点零改动
    badge, holdings_line, payee_row, render_aa_requests,
    render_gift_orders, render_holdings, render_payees, render_schedules,
    T_GIFT_ORDER_EMPTY, T_GIFT_ORDER_HEAD, T_PAYEE_EMPTY, T_PAYEE_HEAD,
    T_PAYEE_LEGEND, T_PAYEE_NO, T_PAYEE_OTHER, T_PAYEE_TABLE_HEAD,
    T_PAYEE_TABLE_SEP, T_PAYEE_WHITELIST, T_PAYEE_YES, T_SCHEDULE_EMPTY,
    T_SCHEDULE_HEAD,
)


# ---------------- 异常检测（F03） ----------------
#: facts 键见 `tools/query.py` T4（`scanned_count` / `anomaly_count` / `items[]`）。
#: F03 验收第 2 条：**命中项必须显示"是哪笔交易 + 凭什么判它"** —— 只报"发现 2 笔异常"用户无从核对。
#: 以句号收尾（2026-10-29）：气泡里只显示这一句时，冒号会让它像"没加载完"；
#: 与空结果 `T_ANOMALY_EMPTY` 的收尾保持一致。
T_ANOMALY_HEAD = "已扫描 {scanned_count} 笔交易，检测到 {anomaly_count} 笔异常交易。"
#: 空结果：自己的句子（与 `T_CARD_NO_CARD` / `T_SUBSCRIPTION_EMPTY` 同口径），
#: 并且**把四条规则的口径一并说清**（阈值仍从 facts 取，不在文案里硬编码任何一个数字）。
T_ANOMALY_EMPTY = ("已扫描 {scanned_count} 笔交易，没有发现异常交易 —— 没有单笔支出超过其近 "
                   "{baseline_days} 天支出均值的 {amount_ratio_threshold} 倍，"
                   "也没有凌晨时段、同商户短时高频或陌生商户的交易。")
#: `TEMPLATES` 里的默认值（实际走 `render_anomalies` 的明细列表分支）。
T_ANOMALY = T_ANOMALY_HEAD

#: 严重程度中文：面向用户不说英文枚举；表外值原样透传（不猜、不编）。
SEVERITY_CN = {"high": "高危", "medium": "关注", "low": "留意"}


def anomaly_line(item: Mapping) -> str:
    """一条异常交易一行（`- ` 开头：界面用 `st.markdown` 渲染会排成项目符号列表）。

    三项内容缺一不可（F03 验收第 2 条「显示交易与依据」）：
    **交易**（时刻 + 对手方 + 金额）→ **依据**（命中了哪几条规则）→ **严重程度**。
    全部照抄 facts 对应项：时刻照抄 `ts` 原串、对手方照抄 `counterparty`（没有对手方的流水
    就整截不印，不补「未知商户」这类编造值）、金额取 `amount_yuan`、依据取工具层冻结的 `reason`。

    金额说「金额」而不说「支出」：T4 只扫**支出**（`amount<0`），`amount_yuan` 自带负号，
    写成「支出 -1,546.00 元」就成了双重否定；照抄带符号的事实值 + 中性措辞最诚实
    （此处**不做任何换算**，取绝对值就等于在渲染层改数字）。
    """
    line = f"- {item['ts']}"
    if item.get("counterparty"):
        line += f"｜{item['counterparty']}"
    severity = SEVERITY_CN.get(str(item["severity"]), str(item["severity"]))
    return f"{line}｜金额 {item['amount_yuan']} 元 —— {item['reason']}（{severity}）"


def render_anomalies(facts: Mapping) -> str:
    """`anomaly_check` 回执（F03）：汇总句 + 每条命中一行。

    分支顺序：**空结果优先**（自己的句子，不套列表头 —— 与 `render_cards` / `render_subscriptions`
    同口径，避免「检测到 0 笔：」这种残句）。空结果必须**明确告知没异常**，不能只报个 0
    （否则用户分不清"没查到"和"查了但没问题"）。
    """
    if not facts["items"]:
        try:
            return T_ANOMALY_EMPTY.format(scanned_count=facts["scanned_count"],
                                          baseline_days=facts["baseline_days"],
                                          amount_ratio_threshold=facts["amount_ratio_threshold"])
        except KeyError as exc:
            raise TemplateError(f"'anomaly_check' 模板缺少 facts 键：{exc}") from exc
    try:
        head = T_ANOMALY_HEAD.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'anomaly_check' 模板缺少 facts 键：{exc}") from exc
    return "\n".join([head, *(anomaly_line(item) for item in facts["items"])])

# ---------------- 卡片查询（卡 23） ----------------
#: facts 键见 `tools/card_query.py`（`total_count` + `items[]`；每项含 `card_no_mask`
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

# ---------------- 理财推荐（F11）→ 已拆到 `agent/render_wealth.py`（文件头 re-import 再导出） ----------------

# ---------------- 订阅列表（卡 20 + F19） ----------------
#: 订阅列表：facts 键见 T10（subscription_count / zombie_count / zombie_window_months / zombie_as_of）
#: 卡 20 追加（人类口径）：面向用户**不说行话** —— 回执里不再出现"僵尸"二字，改成
#: "仍在进行中、但最近 W 个月没有任何扣费记录"。`zombie_*` 只留在代码内部命名与 facts 键里。
#: F19 追加：**回执必须带明细列表**（商户/金额/周期/下次扣费日 = 验收清单第 1 条），
#: 且「进行中」这句只在查 active 时成立（查 cancelled 时套这句会自相矛盾）。
T_SUBSCRIPTION_CLEAN = "当前有 {subscription_count} 个订阅，最近 {zombie_window_months} 个月都有正常扣费记录。"
T_SUBSCRIPTION_ZOMBIE = ("当前有 {subscription_count} 个订阅；其中 {zombie_count} 个仍在「进行中」，"
                         "但最近 {zombie_window_months} 个月没有任何扣费记录 —— 怀疑是您忘了取消的订阅，"
                         "建议核对一下。")
#: 非 active 状态（cancelled / paused）：只报条数与该状态，不解读"是否该取消"。
T_SUBSCRIPTION_FILTERED = "当前有 {subscription_count} 个「{status_cn}」的订阅。"
#: 空结果：自己的句子，不套列表头（与 `T_CARD_NO_CARD` 同口径）。
T_SUBSCRIPTION_EMPTY = "当前没有符合条件的订阅。"
#: `TEMPLATES` 里的默认值（实际按 `status` / `zombie_count` 分支，见 `render_subscriptions`）；
#: 保留这个键位是为了"每个只读意图都有模板"这类检查不落空。
T_SUBSCRIPTION = T_SUBSCRIPTION_ZOMBIE


#: `TemplateError` 已搬到 `render_tables`（本模块依赖它，反向 import 会成环）；
#: 这里仍然再导出，`render_lists.TemplateError` 与 `templates.TemplateError` 是同一个类。


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


def subscription_line(item: Mapping) -> str:
    """一条订阅一行（`- ` 开头：界面用 `st.markdown` 渲染会排成项目符号列表）。

    数字全部照抄 facts 对应项，不做换算：金额取 `amount_yuan` 展示串，周期取 `CYCLE_LABELS`。
    **与 `tools/subscription.py` 的 `_subscription_line` 逐字一致**（`test_subscription_wording`
    有一条测试钉着两层渲染同源）；周期文案直接引工具层常量，不复制副本。
    """
    cycle = CYCLE_LABELS.get(str(item["cycle"]), str(item["cycle"]))
    #: 逾期标记（`active` 但扣费日已过）：不标出来就会出现「提醒说逾期、列表看不出」的矛盾。
    tail = f"（{item['overdue_cn']}）" if item.get("days_overdue") else ""
    return (f"- {item['merchant']}：{item['amount_yuan']} 元／{cycle}，"
            f"下次扣费 {item['next_charge_date']}{tail}")


def render_subscriptions(facts: Mapping) -> str:
    """`subscription_list` 回执（卡 20 + F19）：汇总句 + 明细列表。

    分支顺序：**空结果优先**（自己的句子，不套列表头 —— 与 `render_cards` 同口径，
    避免「当前有 0 个订阅：」这种残句）；再看 `status` —— 查 cancelled / paused 时不套
    「仍在进行中」那句（实测缺陷：查已取消的订阅，回执却说「其中 2 个仍在『进行中』」）；
    最后按 `zombie_count` 分「有忘了取消的」/「都正常」两支。明细行数字全部来自 facts。
    """
    if not facts["items"]:
        return T_SUBSCRIPTION_EMPTY
    status = str(facts.get("status") or "active")
    if status != "active":
        head = T_SUBSCRIPTION_FILTERED.format(subscription_count=facts["subscription_count"],
                                             status_cn=STATUS_CN.get(status, status))
    else:
        chosen = T_SUBSCRIPTION_ZOMBIE if facts.get("zombie_count") else T_SUBSCRIPTION_CLEAN
        try:
            head = chosen.format(**facts)
        except KeyError as exc:
            raise TemplateError(f"'subscription_list' 模板缺少 facts 键：{exc}") from exc
    return "\n".join([head, *(subscription_line(item) for item in facts["items"])])


# ---------------- 理财持仓（T21）+ 全部产品（T22）→ 已拆到 `agent/render_wealth.py` ----------------
