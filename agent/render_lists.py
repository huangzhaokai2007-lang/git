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

__all__ = ["CARD_STATUS_CN", "CARD_TYPE_CN", "CYCLE_LABELS", "TemplateError", "card_line",
           "render_cards", "render_subscriptions", "render_wealth", "subscription_line",
           "wealth_line"]

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
    return (f"- {item['name']}：{item['risk_level']} 级，预期年化 {item['yield']}，"
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
