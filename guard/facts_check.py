"""护栏层：数字校验器（规格 §7，幻觉防护核心；铁律 2）。

一句话：**回执里出现的每个业务数字，都必须能在 `ToolResult.facts` 里找到出处**；找不到就是幻觉。

接口（规格 §7 冻结签名）::

    verify_numbers(reply_text: str, facts: dict) -> tuple[bool, list[str]]

归一化口径（§7「含千分位/万元/百分比归一化」+ 卡 13 明确的「块/元」「整数分 ↔ 元」）：

| 回执写法 | 归一化后的候选值 |
|---|---|
| `1,234.56` | 1234.56 / 123456（元 ↔ 分两端都试） |
| `1.2万` | 12000 / 1200000 |
| `100块`、`100元` | 100 / 10000 |
| `-32%` | -32 / -0.32 |
| `12000分` | 12000 / 120 |

匹配容差（§7：「绝对值相等或为其百分数表达」）：`|回执值| == |事实值|`，或 `== |事实值| × 100`，或 `== |事实值| / 100`。
**日期与时间不是业务数字**（`2026-09-12`、`2026年9月`、`02:57:21`、ISO 时刻）：先摘掉再比 —— 这是卡 09 定下的语义，本卡保持。

未通过 → 由调用方（`agent/templates.compose_reply` / `agent/write_flow`）**重生成一次**；仍未通过 → 降级为模板回执
并把 `HALLUCINATION_BLOCKED` 写进 `audit_log`（编排层已实现，本卡只提供判据）。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Mapping

#: 判据失败时写进 audit_log 的 error_code
ERROR_CODE = "HALLUCINATION_BLOCKED"

#: 日期与时间（不是业务数字）：ISO 时刻、`YYYY-MM-DD`、`YYYY年M月D日`、`H:M(:S)`
_DATEISH = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"
    r"|\d{1,2}:\d{2}(?::\d{2})?")

#: 数值（可带千分位与小数）
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

#: 单位后缀（紧跟数字）：万元 / 块 / 元 / 分 / 百分号
_UNIT = re.compile(r"\s*(万元|元|块|分|%|％)")

#: 允许的容差关系：相等 / 百分数表达（×100）/ 分数表达（÷100）
_RATIOS = (Decimal(1), Decimal(100), Decimal("0.01"))


def _dec(value: object) -> Decimal | None:
    """把 int/float/str 转成 Decimal（非法或 bool → None）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _forms(text: str, token: str, unit: str) -> set[Decimal]:
    """一个回执数字按单位展开成候选值集合（元 ↔ 分两端都试，见模块文档的表格）。"""
    base = _dec(token)
    if base is None:
        return set()
    if unit in ("万元",):
        base *= Decimal(10000)
    elif unit in ("%", "％"):
        return {abs(base), abs(base) / 100}
    elif unit in ("分",):
        return {abs(base), abs(base) / 100}
    return {abs(base), abs(base) * 100}                      # 裸数字/元/块：元与分都试


def reply_tokens(reply_text: str) -> list[tuple[str, set[Decimal]]]:
    """回执里的业务数字：`(原始写法含单位, 归一化候选值集合)`；日期时间先摘掉。"""
    stripped = _DATEISH.sub(" ", reply_text)
    tokens: list[tuple[str, set[Decimal]]] = []
    for match in _NUMBER.finditer(stripped):
        unit_match = _UNIT.match(stripped, match.end())
        unit = unit_match.group(1) if unit_match else ""
        forms = _forms(stripped, match.group(0), unit)
        if forms:
            tokens.append((match.group(0) + unit, forms))
    return tokens


def reply_numbers(reply_text: str) -> set[Decimal]:
    """回执里的业务数字（日期时间先摘掉）→ 归一化候选值（取绝对值，容差由 §7 规定）。"""
    return {value for _token, forms in reply_tokens(reply_text) for value in forms}


def facts_numbers(facts: Mapping | object) -> set[Decimal]:
    """事实包里的全部数字（递归 dict/list，含 `*_yuan` 展示串里的千分位数字）。"""
    found: set[Decimal] = set()

    def walk(node: object) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float, Decimal)):
            value = _dec(node)
            if value is not None:
                found.add(abs(value))
        elif isinstance(node, str):
            for match in _NUMBER.finditer(_DATEISH.sub(" ", node)):
                value = _dec(match.group(0))
                if value is not None:
                    found.add(abs(value))
        elif isinstance(node, Mapping):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                walk(item)

    walk(facts)
    return found


def verify_numbers(reply_text: str, facts: Mapping) -> tuple[bool, list[str]]:
    """规格 §7 的判据：返回 `(是否通过, 越界数字列表)`（越界 = 以原始写法列出，便于审计与排查）。

    逐个回执数字检查：它必须与某个事实数字**绝对值相等**，或等于其 ×100 / ÷100（百分数或分↔元表达）。
    未通过 → 调用方重生成一次；仍不过 → 降级模板回执 + 写 `HALLUCINATION_BLOCKED`。
    """
    known = facts_numbers(facts)
    offenders = [token for token, forms in reply_tokens(reply_text)
                 if not any(abs(value) == abs(fact) * ratio
                            for value in forms for fact in known for ratio in _RATIOS)]
    return (not offenders, offenders)
