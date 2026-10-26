"""**表格类**回执渲染（收款人 / 预约 / 送礼订单 / AA / 持仓）。

2026-10-24 从 `agent/render_lists.py` 拆出（原文件 **426 行**，超 300 红线；本轮又加了状态徽章）。
拆法照仓内先例（`_write_schedule_ops` / `_gift_order`）：**同一份实现、不是副本** ——
`render_lists` 顶部 import 本模块再导出，历史调用点（`templates.render`、测试）零改动。

本模块只做**排版**：所有数字来自事实包（铁律 2），不做业务判断。
"""

from __future__ import annotations

from typing import Mapping
from tools._wealth_risk import RISK_LABELS  # noqa: E402 —— 持仓渲染要用（拆模块时漏带）

__all__ = ["TemplateError", "badge", "holdings_line", "payee_row", "render_aa_requests",
           "render_gift_orders", "render_holdings", "render_payees", "render_schedules"]


class TemplateError(RuntimeError):
    """模板渲染失败（缺占位符 / 键名写错）。**宁可炸**也不许静默留白（静默留白=幻觉的温床）。

    **全仓唯一定义**就在本模块**：`render_lists` 与 `templates` 都从这里 import 再导出，
    因此 `except templates.TemplateError` 与 `pytest.raises(templates.TemplateError)` 命中同一个类。
    2026-10-24：从 `render_lists.py` 搬到这里 —— 本模块的持仓渲染也要用它，而
    `render_lists` 依赖本模块（反向 import 会成环）。
    """


#: **状态徽章**（v3 清单「每张卡的状态徽章」）：状态码 → emoji。
#: 覆盖仓内三套状态机的全部取值（AA 4 · 预约 4 · 送礼订单 4），共 9 个码；
#: **只做视觉标记**，不改任何判定（中文仍由各渲染器自己的词表给）。
_STATUS_EMOJI = {"active": "⏳", "done": "✅", "expired": "⌛", "cancelled": "⛔",
                 "created": "🧾", "paid": "✅", "shipped": "🚚", "completed": "✅",
                 "pending": "⏳", "success": "✅", "failed": "⚠️"}


def badge(status: object, label: str) -> str:
    """把「状态中文」包成带 emoji 的徽章；认不出的状态原样返回（不猜）。"""
    emoji = _STATUS_EMOJI.get(str(status or ""), "")
    return f"{emoji} {label}".strip()


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
#: ⚠️ v2 SPEC-CHANGE：白名单**专属额度内免验证码（含深夜）**；超额度、或短时多笔/金额激增/
#: 首次收款等异常因子，仍会升档要验证码。
T_PAYEE_WHITELIST = "白名单：专属额度内转账免验证码（超额度仍需验证码）"
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


# ---------------- 预约列表（F16 追加：查预约 · T24 只读） ----------------

T_SCHEDULE_EMPTY = "您还没有待执行的预约。说清「哪天、给谁、转多少」，我就帮您预约。"
T_SCHEDULE_HEAD = "您共有 {total_count} 条预约："
def _repeat_cell(item: Mapping) -> str:
    """循环列：`每月（至 2026-12-31）· 下次 2026-11-30` / `一次性`。**纯排版，不算新数字**。"""
    cycle = str(item.get("repeat_cycle") or "")
    if not cycle:
        return "一次性"
    label = {"monthly": "每月", "weekly": "每周"}.get(cycle, cycle)
    until = str(item.get("repeat_until") or "")
    nxt = str(item.get("next_run_at") or "").replace("T", " ")
    parts = [label]
    if until:
        parts.append(f"（至 {until}）")
    if nxt:
        parts.append(f" · 下次 {nxt}")
    return "".join(parts)


T_SCHEDULE_TABLE_HEAD = "| 预约时间 | 收款人 | 金额（元） | 状态 | 循环 |"
T_SCHEDULE_TABLE_SEP = "| --- | --- | --- | --- | --- |"

T_GIFT_ORDER_EMPTY = "您还没有送礼订单。说清送给谁、什么场合、预算多少，我就帮您挑礼物下单。"

T_AA_EMPTY = "您还没有 AA 收款记录。说清「跟谁、收多少」，我就帮您发起。"
T_AA_HEAD = "您共有 {total_count} 笔 AA 收款："
T_AA_TABLE_HEAD = "| 事由 | 合计（元） | 已付 | 状态 | 截止 |"
T_AA_TABLE_SEP = "| --- | --- | --- | --- | --- |"


def render_aa_requests(facts: Mapping) -> str:
    """`aa_list` 回执（T42）：汇总句 + AA 表格（事由/合计/已付人数/状态/截止）。"""
    count = int(facts["total_count"])
    if count == 0:
        return T_AA_EMPTY
    rows = [f"| {item['title']} | {item['total_yuan']} | "
            f"{item['paid_count']}/{item['share_count']} 人 | "
            f"{badge(item['status'], item['status_cn'])} | "
            f"{item['expire_at']} |" for item in facts["items"]]
    return "\n".join([T_AA_HEAD.format(total_count=count), "",
                      T_AA_TABLE_HEAD, T_AA_TABLE_SEP, *rows])
T_GIFT_ORDER_HEAD = "您共有 {total_count} 笔送礼订单："
T_GIFT_ORDER_TABLE_HEAD = "| 订单号 | 收货人 | 送达日期 | 金额（元） | 状态 |"
T_GIFT_ORDER_TABLE_SEP = "| --- | --- | --- | --- | --- |"


def render_gift_orders(facts: Mapping) -> str:
    """`gift_order_list` 回执（T37）：汇总句 + 订单表格（订单号/收货人/日期/金额/状态）。"""
    count = int(facts["total_count"])
    if count == 0:
        return T_GIFT_ORDER_EMPTY
    rows = [f"| {item['order_id']} | {item['contact']} | {item['gift_date']} | "
            f"{item['amount_yuan']} | {badge(item['status'], item['status_cn'])} |"
            for item in facts["items"]]
    return "\n".join([T_GIFT_ORDER_HEAD.format(total_count=count), "",
                      T_GIFT_ORDER_TABLE_HEAD, T_GIFT_ORDER_TABLE_SEP, *rows])
#: 状态中文（纯文案，不含数字；未知状态原样透传，不猜）
_SCHEDULE_STATUS_CN = {"pending": "待执行", "success": "已执行",
                       "failed": "执行失败", "cancelled": "已取消"}


def render_schedules(facts: Mapping) -> str:
    """`scheduled_list` 回执（T24）：汇总句 + **一张预约表格**（时间 / 收款人 / 金额 / 状态）。

    行序照抄 facts 的 `items`（工具层已按执行时间排序）；金额取 `amount_yuan` 展示串，
    时间把 ISO 的 `T` 换成空格（纯排版，不改值）。
    """
    count = int(facts["total_count"])
    if count == 0:
        return T_SCHEDULE_EMPTY
    rows = [f"| {str(item['execute_at']).replace('T', ' ')} | {item['payee_name']} | "
            f"{item['amount_yuan']} | "
            f"{badge(item['status'], _SCHEDULE_STATUS_CN.get(str(item['status']), item['status']))} | "
            f"{_repeat_cell(item)} |"
            for item in facts["items"]]
    return "\n".join([T_SCHEDULE_HEAD.format(total_count=count), "",
                      T_SCHEDULE_TABLE_HEAD, T_SCHEDULE_TABLE_SEP, *rows])


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
