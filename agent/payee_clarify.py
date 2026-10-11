"""F17 收款人解析的追问文案：同名/零匹配时把 `resolve_payee` 的候选**透出给用户**。

修复的问题：T6 早就返回了候选（含脱敏手机号），但编排层只回一句"请补充收款人"，
候选被丢弃 —— 用户看不到是哪两个李四，也就无法选择。

**为什么单独一个模块**：`agent/templates.py` 已顶到 300 行上限（CLAUDE.md「单文件 ≤300 行」），
按 card-04b / card-05b 的先例把这块措辞抽出来。`templates.py` 继续负责只读回执与润色。

铁律 2：全文数字只有 `candidate_count` 与 `candidates[].phone` 两处，**都照抄事实包**，
不换算、不拼接、不补全；绝不自己算"还剩几个"（差值不在 facts 里 = 自造数字）。

**绝不引导"回复序号"**：`data/dao.py` 的 `find_payee` 是**子串匹配**，序号（单个数字）会同时
命中多个手机号（`139****1001` 与 `139****1002` 都含 "1"）→ 再次歧义、变成无限追问。
所以引导用户补充手机号的其中几位（`1001` 能唯一定位 `payee_0001`）。
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from agent import templates
from guard import facts_check

logger = logging.getLogger(__name__)

#: 候选最多列几条（超出只列前 N 条，再补一句"以上为部分结果"）。
CANDIDATES_SHOWN = 5

#: 零匹配的引导语：**不回显用户原话**（不把不可信输入再送回对话）
NO_CANDIDATE = "没有找到匹配的收款人，请确认姓名或手机号，或先添加收款人。"


def render(facts: Mapping) -> str:
    """把 `resolve_payee` 的事实包渲染成追问文案；零匹配 → `NO_CANDIDATE`。

    候选条数与手机号逐字来自 `facts`：`candidate_count` 报总数、`candidates[].name/phone` 逐条列出。
    """
    rows = [row for row in (facts.get("candidates") or []) if isinstance(row, dict)]
    if not rows:
        return NO_CANDIDATE
    total = int(facts.get("candidate_count") or len(rows))
    shown = rows[:CANDIDATES_SHOWN]
    lines = [f"找到 {total} 个同名或相近的收款人，请确认是哪一个："]
    lines += [f"- {row['name']}（{row['phone']}）" for row in shown]
    if total > len(shown):                                     # 只报总数，不报"还剩几条"
        lines.append(f"（以上为部分结果，共 {total} 个匹配）")
    lines.append("请补充对方手机号的其中几位（能区分即可），我再继续为您办理。")
    return "\n".join(lines)


def ask_for(facts: Mapping, missing: Sequence[str] = ()) -> str:
    """缺收款人时的完整追问：候选文案 + 铁律 2 守门 + 其余缺槽的通用追问。

    - 数字越界（文案里的数字在 `facts` 里找不到）→ 返回 `""`，调用方退回通用追问
      （fail-safe，与确认卡「出现 facts 之外的数字就降级为纯事实回执」同一策略）；
    - `missing` 里除 `payee` 外还缺别的槽（如金额）→ 末尾再补一句通用追问，两件事不互相盖掉。
    """
    text = render(facts)
    passed, offenders = facts_check.verify_numbers(text, facts)
    if not passed:
        logger.error("收款人追问出现 facts 之外的数字，退回通用追问：%s", offenders)
        return ""
    others = [name for name in missing if name != "payee"]
    return f"{text}\n{templates.clarify_missing(others)}" if others else text
