"""写操作流（占位）。"""

from __future__ import annotations

WRITE_INTENTS = ()


def inflight(session_id: str) -> str | None:
    return None


def resume(session_id: str, text: str) -> object:
    raise NotImplementedError


def start(intent: str, slots: dict, session_id: str) -> object:
    raise NotImplementedError


def error_code_of(result: object) -> str | None:
    return getattr(result, "error_code", None)
