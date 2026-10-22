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

from tools._wealth_risk import RISK_LABELS
from tools.subscription import (
    CYCLE_LABELS,                        # 周期文案的唯一来源：本模块 import、templates 再导出
    STATUS_LABELS as STATUS_CN,
)

__all__ = ["CARD_STATUS_CN", "CARD_TYPE_CN", "CYCLE_LABELS", "SEVERITY_CN", "TemplateError",
           "anomaly_line", "card_line", "payee_row", "render_all_products", "render_anomalies",
           "render_cards", "render_holdings", "render_payees", "render_subscriptions",
           "render_wealth", "subscription_line", "wealth_line"]

# ---------------- 异常检测（F03） ----------------
#: facts 键见 `tools/query.py` T4（`scanned_count` / `anomaly_count` / `items[]`）。
#: F03 验收第 2 条：**命中项必须显示"是哪笔交易 + 凭什么判它"** —— 只报"发现 2 笔异常"用户无从核对。
T_ANOMALY_HEAD = "已扫描 {scanned_count} 笔交易，检测到 {anomaly_count} 笔异常交易："
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

# ---------------- 理财推荐（F11） ----------------
#: 理财推荐：facts 键见 T14（risk_level / item_count / items[]）
#: F11 追加：**回执必须带产品明细**（产品名/风险等级/预期年化/期限），只报个数用户拿不到可比信息。
#: 措辞里的「推荐」二字是**剧本契约**（`tests/cases/orchestrator.yaml` 的 wlt-001/wlt-002/sec-005
#: 都有 `must_contain: 推荐`），不得改写成「筛选出」等同义词。
T_WEALTH_CLEAN = "按您的风险等级 {risk_level}，为您推荐 {item_count} 个产品："
#: 空结果：自己的句子，不套列表头（与 `T_CARD_NO_CARD` / `T_SUBSCRIPTION_EMPTY` 同口径）；
#: 措辞要**明确说清"没有放宽风险条件"**（F11 验收清单第 2 条）。同样保留「推荐」二字。
T_WEALTH_EMPTY = "按您的风险等级没有可推荐的产品 —— 我没有放宽您的风险等级，您可以放宽期限或金额后再试。"
#: `TEMPLATES` 里的默认值（实际走 `render_wealth` 的明细列表分支）。
T_WEALTH = T_WEALTH_CLEAN

#: 期限的中文：`term_days` 是**天数**（DDL 整数），展示时按「天/月/年」就近换算为**纯文案**，
#: 不参与任何金额或收益计算（收益是工具层给的百分数字符串，本模块只照抄）。
TERM_DEMAND = 0               # 无固定期限（seed 的货币基金 `term_days=0`）→ 说「活期」而不是「0 天」
TERM_DAY_CUTOFF = 30          # 不足一个月按天说
TERM_MONTH_CUTOFF = 365       # 不足一年按月说
DAYS_PER_MONTH = 30
DAYS_PER_YEAR = 365


def term_cn(term_days: object) -> str:
    """持有期限的中文展示（0 → 「活期」、90 → 「3 个月」、365 → 「1 年」、720 → 「720 天」）。

    **只做单位换算，不重算任何业务数字**：`term_days` 本身就是 facts 里的那个整数，
    这里只是把它换成用户好读的说法（与 `CYCLE_LABELS` 把 `monthly` 说成「每月」同性质）。

    两条已实测的坑（都是**数字校验器**先抓到的 —— 换算出来的数字若不在 facts 里即为幻觉）：
    - `term_days=0` 的货币基金曾显示成「期限 0 天」，读起来像"没有期限"→ 改说「活期」；
    - 720 天曾显示成「1.9726 年」—— **浮点数**，既违反项目红线「禁浮点」，又因 `1.9726` 不在
      facts 里而被 `verify_numbers` 判为幻觉、触发降级。故**年/月只在能整除时才换算**，
      除不尽就老实按天说（宁可说「720 天」，绝不编一个带小数的年数）。
    """
    days = int(term_days)
    if days <= TERM_DEMAND:
        return "活期"
    if days < TERM_DAY_CUTOFF:
        return f"{days} 天"
    if days < TERM_MONTH_CUTOFF and days % DAYS_PER_MONTH == 0:
        return f"{days // DAYS_PER_MONTH} 个月"
    if days % DAYS_PER_YEAR == 0:
        return f"{days // DAYS_PER_YEAR} 年"
    return f"{days} 天"


def wealth_line(item: Mapping) -> str:
    """一个理财产品一行（`- ` 开头：界面用 `st.markdown` 渲染会排成项目符号列表）。

    数字全部照抄 facts 对应项：预期年化取工具层格式化好的 `yield` 字符串（**不在这里做浮点运算**，
    项目红线「金额一律整数分、禁浮点」），期限取 `term` 整数经 `term_cn` 换成文案。
    """
    level = str(item["risk_level"])
    return (f"- {item['name']}：{RISK_LABELS.get(level, level)}，预期年化 {item['yield']}，"
            f"期限 {term_cn(item['term'])}")


def render_wealth(facts: Mapping) -> str:
    """`wealth_recommend` 回执（F11）：汇总句 + 产品明细列表。

    分支顺序：**空结果优先**（自己的句子，不套列表头 —— 与 `render_cards` / `render_subscriptions`
    同口径，避免「筛出 0 个产品：」这种残句）。**绝不为了凑数放宽风险等级**：空就是空，
    措辞里明说没有放宽（验收清单第 2 条）。明细行数字全部来自 facts。
    """
    if not facts["items"]:
        return T_WEALTH_EMPTY
    try:
        head = T_WEALTH_CLEAN.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'wealth_recommend' 模板缺少 facts 键：{exc}") from exc
    return "\n".join([head, *(wealth_line(item) for item in facts["items"])])


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


class TemplateError(RuntimeError):
    """模板渲染失败（缺占位符 / 键名写错）。**宁可炸**也不许静默留白（静默留白=幻觉的温床）。

    **与 `agent/templates.TemplateError` 是同一个类**：本模块的定义先于 `templates.py` 执行，
    `templates.py` 从这里 import 并再导出 —— 因此 `except templates.TemplateError` 仍然命中，
    `pytest.raises(templates.TemplateError)` 也不会因拆文件而失效。
    """


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
    return (f"- {item['merchant']}：{item['amount_yuan']} 元／{cycle}，"
            f"下次扣费 {item['next_charge_date']}")


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


# ---------------- 收款人 / 白名单（T20 · SPEC-CHANGE 新增） ----------------
#: facts 键见 `tools/payee.py:list_payees`（`total_count` / `whitelist_count` /
#: `non_whitelist_count` / `items[]`）。空结果**自己的句子**，不套列表头
#: （与 `T_CARD_NO_CARD` / `T_SUBSCRIPTION_EMPTY` 同口径，避免「共 0 位：」这种残句）。
T_PAYEE_HEAD = "您共有 {total_count} 位收款人：白名单 {whitelist_count} 位、其它 {non_whitelist_count} 位。"
T_PAYEE_EMPTY = "您还没有收款人。可以说「我要加个收款人」来添加。"
#: 排版改为**精简表格**（用户要求）：原先的「【分组】+ 项目符号」一行一位、看着像流水账，
#: 表格让「姓名 / 手机号 / 银行 / 是否白名单」四列对齐。分组不再靠小标题，而是靠
#: 「白名单」列 + 工具层既有的排序（白名单优先）表达 —— 语义不变，行序一个字没重排。
T_PAYEE_TABLE_HEAD = "| 收款人 | 手机号 | 银行 | 白名单 |"
T_PAYEE_TABLE_SEP = "| --- | --- | --- | --- |"
#: 单元格文案：**都不含数字**。`T_PAYEE_NO` 同时用于「非白名单」与「银行未填」，
#: 它只是占位符，**不是**编造的银行名（规格：`bank` 为空不补「未知银行」这类值）。
T_PAYEE_YES, T_PAYEE_NO = "✅", "—"
#: 表下**唯一一行**说明：两组的转账验证差别（这是用户看这张表的实际用途）；纯文案，不含数字。
T_PAYEE_WHITELIST = "白名单：转账只需会话内确认"
T_PAYEE_OTHER = "非白名单：转账需确认卡 + 短信验证码"
T_PAYEE_LEGEND = f"{T_PAYEE_WHITELIST}；{T_PAYEE_OTHER}。"


def payee_row(item: Mapping) -> str:
    """一位收款人一行（Markdown 表格行）。

    手机号**照抄 facts 的脱敏值**（`139****1001`），不拼接、不补全、**绝不还原完整号**（铁律 8）；
    银行名照抄入库值，为空则印 `—` 占位（不补「未知银行」这类编造值）；「白名单」列只用 ✅ / —。
    """
    bank = str(item.get("bank") or "") or T_PAYEE_NO
    mark = T_PAYEE_YES if item["is_whitelist"] else T_PAYEE_NO
    return f"| {item['name']} | {item['masked_phone']} | {bank} | {mark} |"


def render_payees(facts: Mapping) -> str:
    """`payee_list` 回执（T20）：汇总句 + **一张精简表格** + 一行分组说明。

    行序**照抄 facts 的 `items` 顺序**（工具层已按「白名单优先 + 姓名」稳定排序），编排层不重排
    —— 重排就是自己造顺序。「白名单」列让用户一眼看出哪些是白名单（两组的转账验证方式不同）。
    """
    if int(facts["total_count"]) == 0:
        return T_PAYEE_EMPTY
    head = T_PAYEE_HEAD.format(total_count=int(facts["total_count"]),
                               whitelist_count=int(facts["whitelist_count"]),
                               non_whitelist_count=int(facts["non_whitelist_count"]))
    rows = [T_PAYEE_TABLE_HEAD, T_PAYEE_TABLE_SEP,
            *(payee_row(item) for item in facts["items"])]
    return "\n".join([head, "", *rows, "", T_PAYEE_LEGEND])


# ---------------- 理财持仓（T21）+ 全部产品（T22） ----------------

T_HOLDINGS_EMPTY = "您目前没有持有中的理财产品。"
T_HOLDINGS_HEAD = "您目前持有 {total_count} 只理财产品，合计 {total_amount_yuan} 元："


def holdings_line(item: Mapping) -> str:
    """一行持仓：产品名、金额、风险等级（中文）、年化、买入日。数字全部照抄 facts。"""
    level = str(item["risk_level"])
    return (f"- {item['name']}：{item['amount_yuan']} 元，"
            f"{RISK_LABELS.get(level, level)}，预期年化 {item['yield']}，"
            f"买入于 {item['purchase_date']}")


def render_holdings(facts: Mapping) -> str:
    """`wealth_holdings` 回执（T21）：空结果优先（不套列表头），再汇总句 + 明细。"""
    if not facts["items"]:
        return T_HOLDINGS_EMPTY
    try:
        head = T_HOLDINGS_HEAD.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'wealth_holdings' 模板缺少 facts 键：{exc}") from exc
    return "\n".join([head, *(holdings_line(item) for item in facts["items"])])


def render_all_products(facts: Mapping) -> str:
    """T22：全部产品，按「适合您」/「超出等级」分组；后者明确标注**不建议购买**。

    与 `render_wealth`（T14 推荐）的区别：推荐只列等级内的、按收益降序；
    这里列全部、按等级从低到高，**不做收益排序**（避免变成变相推荐）。
    """
    fit = [item for item in facts["items"] if item["recommended"]]
    over = [item for item in facts["items"] if not item["recommended"]]
    lines = [f"共 {facts['total_count']} 只产品，其中 {facts['fit_count']} 只在您的风险等级"
             f"（{facts['user_risk_label']}）内。", "", "**适合您的**"]
    lines += [wealth_line(item) for item in fit] or ["- 暂无"]
    if over:
        lines += ["", "**超出您的风险等级（仅供参考，不建议购买）**"]
        lines += [wealth_line(item) for item in over]
    return "\n".join(lines)
