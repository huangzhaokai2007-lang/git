"""相对时间说法 → ISO8601（「明天」「下周一」「下个月 5 号」「月底」「晚上 8 点」）。

**确定性规则解析**（不猜、不调 LLM）：认不出 → `None`，由调用方追问格式。
所有计算基于传入的 `now`（编排层传 `tools.transfer._now()`，测试里可钉住）——
不读系统时钟，保证换机器/换时刻结果一致。

口径（照用户说法直译）：
| 说法 | 结果 |
|---|---|
| 今天 / 明天 / 后天 / 大后天 | +0 / +1 / +2 / +3 天 |
| N 天后（含「N 天之后」） | +N 天 |
| 周一…周日（无前缀，已过则下周） | 最近的那一天 |
| 下周一 / 这周三 / 本周日 | 对应那一天 |
| 下个月 5 号 / 本月 20 日 | 指定月日（越界取月末） |
| 月底 / 月末（本月已过则下月） | 当月最后一天 |
| 年底 / 今年底 / 明年底 | 当年 12-31（已过则次年）/ 次年 12-31 |
| 时刻：9 点 / 15:00 / 晚上 8 点 / 下午 3 点 / 上午 10 点 | 缺省 **09:00** |
"""

from __future__ import annotations

import calendar
import datetime
import re

#: 相对天数
_DAY_WORDS = {"大后天": 3, "今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}
_DAY_AFTER = re.compile(r"(\d+)\s*天(?:之|以)?后")
#: 星期
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
            "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}
_WEEK = re.compile(r"(下下|下|这|本|上)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天1-7])")
#: 指定月日
_MONTH_DAY = re.compile(r"(下个?月|本月|这个月)\s*(\d{1,2})\s*[号日]")
#: 时刻
_CLOCK = re.compile(r"(上午|早上|早晨|中午|下午|傍晚|晚上|夜里)?\s*(\d{1,2})\s*[:：点时]\s*(\d{1,2})?\s*分?")
_PM = ("下午", "傍晚", "晚上", "夜里")


def _add_months(year: int, month: int, shift: int) -> tuple[int, int]:
    """月份加减（1..12 环绕）。"""
    index = year * 12 + (month - 1) + shift
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def next_month_end(now: datetime.datetime) -> datetime.date:
    """**最近**的月末：本月月末还没过就取本月，否则下月月末（「每个月月末」的首期）。"""
    last = calendar.monthrange(now.year, now.month)[1]
    candidate = datetime.date(now.year, now.month, last)
    if now.date() < candidate:
        return candidate
    year, month = _add_months(now.year, now.month, 1)
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def parse(raw: object, now: datetime.datetime) -> str | None:
    """把一句话里的相对时间解析成 ISO（分精度）；认不出 → None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    day: datetime.date | None = None

    for word, delta in _DAY_WORDS.items():                      # 今天/明天/后天/大后天
        if word in text:
            day = now.date() + datetime.timedelta(days=delta)
            break
    if day is None and (match := _DAY_AFTER.search(text)):
        day = now.date() + datetime.timedelta(days=int(match.group(1)))
    if day is None and (match := _WEEK.search(text)):           # 周X / 下周X / 本周X
        prefix, name = match.group(1) or "", match.group(2)
        target = _WEEKDAY[name]
        if prefix.startswith("下"):
            day = now.date() + datetime.timedelta(days=(7 - now.weekday()) + target)
        elif prefix == "上":
            day = now.date() - datetime.timedelta(days=now.weekday() - target + 7)
        else:
            delta = target - now.weekday()
            day = now.date() + datetime.timedelta(days=delta if delta > 0 else delta + 7)
    if day is None and (match := _MONTH_DAY.search(text)):      # 下个月 5 号 / 本月 20 日
        year, month = _add_months(now.year, now.month, 1 if match.group(1).startswith("下") else 0)
        day = datetime.date(year, month,
                            min(int(match.group(2)), calendar.monthrange(year, month)[1]))
    if day is None and "年底" in text:                          # 年底 / 今年底 / 明年底
        year = now.year + (1 if text.startswith("明") else 0)
        candidate = datetime.date(year, 12, 31)
        if candidate <= now.date() and not text.startswith("明"):
            candidate = datetime.date(year + 1, 12, 31)
        day = candidate
    if day is None and ("月底" in text or "月末" in text):        # 月底 / 月末
        last = calendar.monthrange(now.year, now.month)[1]
        candidate = datetime.date(now.year, now.month, last)
        day = candidate if now.date() <= candidate else next_month_end(now)
    if day is None:
        return None

    hour, minute = 9, 0                                          # 缺省 09:00
    if (match := _CLOCK.search(text)):
        period, hour, minute = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        if period in _PM and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        hour, minute = hour % 24, minute
    return datetime.datetime(day.year, day.month, day.day, hour, minute).isoformat(timespec="minutes")
