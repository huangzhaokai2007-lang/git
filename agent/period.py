"""相对时间槽位归一化（卡 17b）：`period` / 日期取值的**唯一一份**口径 —— 纯代码、无状态、无 IO。

为什么单独一层：编排层（`agent/orchestrator.py`）只是调用方；分出来后这张别名表有了明确的家，
编排层也能守住"单文件 ≤300 行"的规范（本卡前 orchestrator 已被挤到 343 行）。

口径（卡 17b 现场实测的静默错月）：

- 中/英/下划线/连字符变体 → 规范说法（`本月` / `上个月` / `上上个月`）查 `RELATIVE_PERIOD_ALIASES`；
- **认不出的相对说法一律不猜**：`resolve_period` / `resolve_day` 返回 `None`，由调用方（编排层）
  判缺失 → CLARIFY 追问 —— 旧实现"认不出就回落锚点当月"，把「上个月」答成「本月 0.00 元」，
  静默、偶发、数字还来自真实事实包，看着完全像对的；
- 显式月份（`2026-08` / `2026`）原样透传；时间锚 = `data.seed.AS_OF`。
"""

from __future__ import annotations

import calendar
import re

from data.seed import AS_OF

#: 相对时间的**取值别名表**（口径同 `classifier.SLOT_VALUE_ALIASES`：LLM 填什么都可以，代码说了算）。
#: 真机实测：模型（温度 0）稳定返回英文 `last_month`，所以这张表不是"以防万一"而是主路径。
RELATIVE_PERIOD_ALIASES: dict[str, str] = {
    "本月": "本月", "这个月": "本月", "当月": "本月",
    "this_month": "本月", "thismonth": "本月", "current_month": "本月", "currentmonth": "本月",
    "上个月": "上个月", "上月": "上个月",
    "last_month": "上个月", "lastmonth": "上个月", "previous_month": "上个月", "prev_month": "上个月",
    "上上个月": "上上个月", "上上上月": "上上个月",
    "last_2_months": "上上个月", "last_two_months": "上上个月", "two_months_ago": "上上个月",
}

#: 别名归一到规范说法后，锚点月的偏移（唯一一份；`resolve_period` / `resolve_day` 共用）
RELATIVE_PERIOD_OFFSETS: dict[str, int] = {"本月": 0, "上个月": -1, "上上个月": -2}

#: 查表键归一化：小写、去首尾空白、空格/连字符 → 下划线（`last-2-months`、`Last Month` 也认）
_PERIOD_KEY_SEPARATOR = re.compile(r"[\s\-]+")


def month_shift(offset: int) -> str:
    """锚点月偏移 → `YYYY-MM`（时间锚 = `data.seed.AS_OF`）。"""
    total = AS_OF.year * 12 + (AS_OF.month - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def normalize_relative_period(value: object) -> str | None:
    """把相对时间说法归一成 `本月` / `上个月` / `上上个月`；认不出 → `None`（**不猜**）。"""
    key = _PERIOD_KEY_SEPARATOR.sub("_", str(value or "").strip().lower())
    return RELATIVE_PERIOD_ALIASES.get(key)


def anchor_period() -> str:
    """锚点当月：`period` **完全没给**时的缺省口径（卡 09，未变）。"""
    return month_shift(0)


def resolve_period(value: object) -> str | None:
    """`period` 槽位 → 工具接受的 `YYYY-MM` / `YYYY`；**相对说法认不出 → `None`**（卡 17b）。"""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", text) or re.fullmatch(r"\d{4}", text):
        return text
    canonical = normalize_relative_period(text)
    return None if canonical is None else month_shift(RELATIVE_PERIOD_OFFSETS[canonical])


def resolve_day(value: object, *, edge: str) -> str | None:
    """日期槽位 → `YYYY-MM-DD`：ISO 原样；相对说法 → 该月首/末日；其它 → `None`（判为缺失）。"""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    canonical = normalize_relative_period(text)
    if canonical is None:
        return None
    period = month_shift(RELATIVE_PERIOD_OFFSETS[canonical])
    year, month = int(period[:4]), int(period[5:])
    day = 1 if edge == "start" else calendar.monthrange(year, month)[1]
    return f"{period}-{day:02d}"
