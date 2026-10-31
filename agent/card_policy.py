"""「转账必须先选收款卡」的策略开关（只对网页生效，agent 契约不变）。

用户 2026-10-31：转账/定时转账卡面前**必须选**收款人的银行卡，取消"默认卡"。
但 agent 层大量用例与 `/api/chat`、IM 通道都假设"给名字+金额就出卡"，
强行全局要求会连带把它们打断 —— 所以做成**网页显式打开**的开关：
- `interfaces/web/app.py` 每次渲染 `set_require_payee_card(True)`；
- agent 层/用例默认 False（保持既有契约）。
"""

from __future__ import annotations

_REQUIRE = False


def set_require_payee_card(enabled: bool) -> None:
    global _REQUIRE
    _REQUIRE = bool(enabled)


def require_payee_card() -> bool:
    return _REQUIRE
