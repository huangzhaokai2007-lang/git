"""护栏层：数字校验器（规格 §7，幻觉防护核心；铁律 2）。

一句话：**回执里出现的每个业务数字，都必须能在 `ToolResult.facts` 里找到出处**；找不到就是幻觉。

接口（规格 §7 冻结签名）::

    verify_numbers(reply_text: str, facts: dict) -> tuple[bool, list[str]]

容差口径（§7，SPEC-CHANGE 收窄：**容差由该回执数字的单位决定**）：

| 回执写法 | 允许的匹配关系（"事实" = `facts` 里的数字） |
|---|---|
| `10000`（裸数字） | **只与事实精确相等**，不跨单位放行 |
| `1,234.56 元`、`100块` | 相等，或 = 事实 ÷ 100（回执写元、事实存分） |
| `12000分` | 相等，或 = 事实 × 100（回执写分、事实存元） |
| `-32%` | 相等，或 = 事实 × 100（百分数 ↔ 小数，如 `-32` ↔ `-0.32`） |
| `1.2万元` | 先 ×10000 展开再比（`1.2万元` = `12000` 元 = `1,200,000` 分） |

**为什么收窄**：旧口径对裸数字也套 ×100 / ÷100，于是回执写 `10000`（想说 1 万元）能对上事实
`1000000`（分）—— 单位不明的数字被跨单位"蒙"过去，等于给幻觉留后门。现在**只有带单位**才允许跨单位。

**日期与时间不是业务数字**（`2026-09-12`、`2026年9月`、`02:57:21`、ISO 时刻）：先摘掉再比 —— 卡 09 定下的语义。

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

#: 单位 → 允许的容差比率（乘在**事实**上）。裸数字只有 `(1,)`：不跨单位。
_RATIOS_BY_UNIT: dict[str, tuple[Decimal, ...]] = {
    "": (Decimal(1),),                                    # 裸数字：只精确匹配
    "元": (Decimal(1), Decimal("0.01")),                  # 元 ↔ 分（事实存分时 ÷100）
    "块": (Decimal(1), Decimal("0.01")),
    "万元": (Decimal(1), Decimal("0.01")),                # 先 ×10000 展开，再允许元 ↔ 分
    "分": (Decimal(1), Decimal(100)),                     # 分 ↔ 元（事实存元时 ×100）
    "%": (Decimal(1), Decimal(100)),                      # 百分数 ↔ 小数
    "％": (Decimal(1), Decimal(100)),
}

#: 单位 → 回执数字先按此系数展开（只有 `万元` 需要；其余单位在比率里体现）
_SCALE_BY_UNIT: dict[str, Decimal] = {"万元": Decimal(10000)}

#: 单位只有一个字符时的兜底比率（`_UNIT` 只可能匹配出上表里的单位，这里纯防御）
_FALLBACK_RATIOS: tuple[Decimal, ...] = (Decimal(1),)


def _dec(value: object) -> Decimal | None:
    """把 int/float/str 转成 Decimal（非法或 bool → None）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _forms(token: str, unit: str) -> set[Decimal]:
    """回执数字按单位展开成候选值（取绝对值；只有 `万元` 需要先 ×10000）。"""
    base = _dec(token)
    if base is None:
        return set()
    return {abs(base) * _SCALE_BY_UNIT.get(unit, Decimal(1))}


def _ratios(unit: str) -> tuple[Decimal, ...]:
    """该单位允许的容差比率（未识别单位 → 只精确匹配）。"""
    return _RATIOS_BY_UNIT.get(unit, _FALLBACK_RATIOS)


def reply_tokens(reply_text: str) -> list[tuple[str, str, set[Decimal]]]:
    """回执里的业务数字：`(原始写法含单位, 单位, 归一化候选值集合)`；日期时间先摘掉。"""
    stripped = _DATEISH.sub(" ", reply_text)
    tokens: list[tuple[str, str, set[Decimal]]] = []
    for match in _NUMBER.finditer(stripped):
        unit_match = _UNIT.match(stripped, match.end())
        unit = unit_match.group(1) if unit_match else ""
        forms = _forms(match.group(0), unit)
        if forms:
            tokens.append((match.group(0) + unit, unit, forms))
    return tokens


def reply_numbers(reply_text: str) -> set[Decimal]:
    """回执里的业务数字（日期时间先摘掉）→ 归一化候选值（按各自单位展开，见模块文档）。"""
    return {value for _token, _unit, forms in reply_tokens(reply_text) for value in forms}


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

    逐个回执数字检查：**按它自己的单位**选容差（裸数字只精确匹配），能与某个事实数字对上即通过。
    未通过 → 调用方重生成一次；仍不过 → 降级模板回执 + 写 `HALLUCINATION_BLOCKED`。
    """
    known = facts_numbers(facts)
    offenders = [token for token, unit, forms in reply_tokens(reply_text)
                 if not any(abs(value) == abs(fact) * ratio
                            for value in forms for fact in known for ratio in _ratios(unit))]
    return (not offenders, offenders)
