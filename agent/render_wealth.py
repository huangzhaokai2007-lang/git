"""理财**列表回执**渲染（从 `agent/render_lists.py` 拆出，2026-10-29）。

为什么单开一个文件：推荐回执改为**按产品大类分组**后，`render_lists.py` 越过 300 行红线
（CLAUDE.md 第 51 行）。照 `agent/read_routes.py`（卡 23 拆 `TOOL_ROUTES`）的先例：
**同一份实现**搬过来，`render_lists` 反向 import 再导出，历史调用点（`templates`、测试）零改动。

分层：本模块属编排层，只 import `tools/` 的纯常量与文案；不碰 `data/`、不写 SQL。
数字纪律（铁律 2）：每一行明细都**照抄 facts**，不做换算、不做拼接、不补全
（收益是工具层给的百分数字符串，这里只照抄）。
"""

from __future__ import annotations

from typing import Mapping

from agent.render_tables import TemplateError
from tools._wealth_products import CATEGORY_LABELS, CATEGORY_ORDER  # 分组顺序的真源
from tools._wealth_risk import RISK_LABELS

__all__ = ["T_WEALTH", "T_WEALTH_CLEAN", "T_WEALTH_EMPTY", "category_label",
           "render_all_products", "render_wealth", "term_cn", "wealth_groups",
           "wealth_line"]


# ---------------- 理财推荐（F11） ----------------
#: 理财推荐：facts 键见 T14（risk_level / item_count / items[]）
#: F11 追加：**回执必须带产品明细**（产品名/风险等级/预期年化/期限），只报个数用户拿不到可比信息。
#: 措辞里的「推荐」二字是**剧本契约**（`tests/cases/orchestrator.yaml` 的 wlt-001/wlt-002/sec-005
#: 都有 `must_contain: 推荐`），不得改写成「筛选出」等同义词。
T_WEALTH_CLEAN = "按您的风险等级「{risk_label}」，为您推荐 {item_count} 个产品（按产品大类分组）："
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


def wealth_groups(items) -> list[tuple[str, list]]:
    """把产品按**规范大类**分组（组序 = `CATEGORY_ORDER` 稳健 → 进取，组内保持 facts 原序）。

    为什么分组放在编排层而不是界面：分组只是**展示顺序**，排序的键（大类）与顺序口径
    都来自工具层常量；界面（interfaces/**）只 import 本函数排版，不自己分类
    （铁律 1：口径只有工具层一份）。不在 `CATEGORY_ORDER` 里的大类兜底排最后（表外值
    原样透传，不猜、不编）。
    """
    buckets: dict[str, list] = {}
    for item in items:
        buckets.setdefault(str(item.get("category") or ""), []).append(item)
    groups = [(cat, buckets[cat]) for cat in CATEGORY_ORDER if cat in buckets]
    groups += [(cat, group) for cat, group in buckets.items() if cat not in CATEGORY_ORDER]
    return groups


def category_label(category: object) -> str:
    """大类键 → 中文标签（表外值原样透传；空串兜底叫「其他」）。"""
    cat = str(category or "")
    return CATEGORY_LABELS.get(cat, cat) if cat else "其他"


#: 结构化产品的气泡提示按细分类型分话术：鲨鱼鳍是**结构性存款**（保本、区间计息），
#: 雪球是**收益凭证**（非保本、敲入承担亏损）—— 一律不说成"基金"（2026-10-29 查证修正）。
_STRUCTURE_HINTS = {
    "sharkfin": "（结构性存款：存款保本 + 期权博浮动收益，条款详见卡片）",
    "snowball": "（雪球收益凭证：非保本，含敲入敲出条款，详见卡片细则）",
}


def wealth_line(item: Mapping) -> str:
    """一个理财产品一行（`- ` 开头：界面用 `st.markdown` 渲染会排成项目符号列表）。

    数字全部照抄 facts 对应项：预期年化取工具层格式化好的 `yield` 字符串（**不在这里做浮点运算**，
    项目红线「金额一律整数分、禁浮点」），期限取 `term` 整数经 `term_cn` 换成文案。
    """
    level = str(item["risk_level"])
    #: 类型标签（结构化/货币型/…）：货架扩到 25 只后，光看名字分不清是什么东西。
    kind = str(item.get("kind_label") or "")
    line = (f"- {item['name']}：{RISK_LABELS.get(level, level)}"
            f"{'，' + kind if kind else ''}，预期年化 {item['yield']}，"
            f"期限 {term_cn(item['term'])}")
    if item.get("structure"):
        line += _STRUCTURE_HINTS.get(str(item.get("kind")),
                                     "（结构化产品：含敲入敲出等障碍条款，详见卡片细则）")
    #: 演示区间只是模型规则假设，不是实测最大亏损或历史最高收益。
    if item.get("max_gain_pct") is not None and item.get("max_loss_pct") is not None:
        line += (f"\n  （模拟参考区间：↑ 上沿 {item['max_gain_pct']}%"
                 f" / ↓ 下沿 {item['max_loss_pct']}%；非历史极值或收益承诺）")
    return line


def render_wealth(facts: Mapping) -> str:
    """`wealth_recommend` 回执（F11）：汇总句 + 产品明细列表。

    分支顺序：**空结果优先**（自己的句子，不套列表头 —— 与 `render_cards` / `render_subscriptions`
    同口径，避免「筛出 0 个产品：」这种残句）。**绝不为了凑数放宽风险等级**：空就是空，
    措辞里明说没有放宽（验收清单第 2 条）。明细行数字全部来自 facts。
    **按产品大类分组**展示（2026-10-29 用户要求"推荐必须分类，不要毫无规律扔出来"）：
    组序稳健 → 进取（工具层 `CATEGORY_ORDER`），组内仍按收益降序（facts 原序）。
    """
    if not facts["items"]:
        return T_WEALTH_EMPTY
    try:
        head = T_WEALTH_CLEAN.format(**facts)
    except KeyError as exc:
        raise TemplateError(f"'wealth_recommend' 模板缺少 facts 键：{exc}") from exc
    lines = [head]
    for category, group in wealth_groups(facts["items"]):
        lines += ["", f"**{category_label(category)}**",
                  *(wealth_line(item) for item in group)]
    return "\n".join(lines)


# ---------------- 理财持仓（T21）+ 全部产品（T22） ----------------

def render_all_products(facts: Mapping) -> str:
    """T22：全部产品，按「适合您」/「超出等级」分组；后者明确标注**不建议购买**。

    与 `render_wealth`（T14 推荐）的区别：推荐只列等级内的、按收益降序；
    这里列全部、按等级从低到高，**不做收益排序**（避免变成变相推荐）。
    两段各自再**按产品大类分组**（同 `render_wealth`，2026-10-29）。
    """
    fit = [item for item in facts["items"] if item["recommended"]]
    over = [item for item in facts["items"] if not item["recommended"]]
    lines = [f"共 {facts['total_count']} 只产品，其中 {facts['fit_count']} 只在您的风险等级"
             f"（{facts['user_risk_label']}）内。", "", "**适合您的**"]
    if fit:
        for category, group in wealth_groups(fit):
            lines += ["", f"**{category_label(category)}**",
                      *(wealth_line(item) for item in group)]
    else:
        lines += ["- 暂无"]
    if over:
        lines += ["", "**超出您的风险等级（仅供参考，不建议购买）**"]
        for category, group in wealth_groups(over):
            lines += ["", f"**{category_label(category)}**",
                      *(wealth_line(item) for item in group)]
    return "\n".join(lines)
