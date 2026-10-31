"""演示时钟：把「现在几点」钉在**白天工作日 12:00**（只影响演示环境，风控规则一条不改）。

为什么需要：规格 §5 的 `night(23:00-06:00)` 因子会把"深夜演示"的每笔转账顶到 L3（待人工复核），
答辩论/PPT 排练多在夜里，演示会频繁卡在"等 60s 复核"。白天演示则一路走 L1/L2 正常流程。

**这不是绕过风控**：
- 五档权限、OTP、限额、白名单、审计日志，一条没动；
- 只是把"系统认为现在几点"设成 12:00（相当于演示环境的时间设置）；
- 默认**开启**（本仓库整体就是合成数据演示），可在侧边栏关掉，关掉即回到真实时间；
- 界面会显式标注「演示时钟 12:00」，评审专家一眼能看见。

分层：放在 guard/ 里 —— 	ools/ 可以调 guard/，避免 tools→agent 回边。

用法：`demo_clock.set_moment(datetime(...))` / `demo_clock.clear()` / `demo_clock.moment()`。
"""

from __future__ import annotations

from datetime import datetime

#: 演示用的固定时刻：一个**工作日中午**，避开 night/凌晨 因子
DEMO_MOMENT = datetime(2026, 9, 12, 12, 0, 0)

_moment: datetime | None = None


def set_moment(moment: datetime | None = None) -> datetime:
    """打开演示时钟（不传就用 `DEMO_MOMENT`）。"""
    global _moment
    _moment = moment or DEMO_MOMENT
    return _moment


def clear() -> None:
    """关掉演示时钟，回到真实时间。"""
    global _moment
    _moment = None


def enabled() -> bool:
    return _moment is not None


def moment(real: datetime | None = None) -> datetime:
    """风控看到的"现在"：演示时钟开着就用它，否则用真实时间。"""
    if _moment is not None:
        return _moment
    return real or datetime.now()


def label() -> str:
    """给界面用的一句话（没开就返回空串）。"""
    if _moment is None:
        return ""
    return _moment.strftime("%Y-%m-%d %H:%M")
