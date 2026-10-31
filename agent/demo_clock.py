"""演示时钟的 agent 侧入口（实现放在 guard/demo_clock.py，避免 tools→agent 回边）。"""

from __future__ import annotations

from guard.demo_clock import (  # noqa: F401  （对外转发）
    DEMO_MOMENT,
    clear,
    enabled,
    label,
    moment,
    set_moment,
)
