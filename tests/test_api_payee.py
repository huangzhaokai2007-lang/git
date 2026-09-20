"""卡 20 追加单测：`POST /api/payee`（渠道无关入口）+ `/api/chat` 的 6 字段契约**不变**。

做法与 `scripts/api_smoke.py` 同路数：按 ASGI 协议直连应用（不联网、不占端口、不需要 LLM key）；
只复用冒烟脚本里的 `call` 驱动器（纯驱动、不含断言）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import api_smoke                                                    # noqa: E402

from agent import orchestrator                                      # noqa: E402
from interfaces.api.app import RESPONSE_FIELDS, create_app           # noqa: E402
from tests.conftest import count, raw                               # noqa: E402

FULL, MASKED = "13812345678", "138****5678"


def test_payee_endpoint_has_the_same_six_fields_and_writes_the_row(seeded: Path) -> None:
    status, body = api_smoke.call(create_app(), "POST", "/api/payee", {"name": "王小明", "phone": FULL})
    assert status == 200
    assert tuple(body) == RESPONSE_FIELDS                            # 与 /api/chat **同形状**（恰好 6 个字段）
    assert (body["intent"], body["tier"], body["executed"]) == ("payee_add", "L1", True)
    assert body["tool_calls"] == ["add_payee"]
    assert MASKED in body["reply"] and FULL not in body["reply"]      # 只出现脱敏号
    row = raw(seeded, "SELECT * FROM payee WHERE name = ?", ("王小明",))[0]
    assert row["phone"] == MASKED and FULL not in str(dict(row))


def test_payee_endpoint_delegates_to_the_agent_entry(seeded: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """渠道无关的硬要求：端点与 Streamlit 表单走**同一个** agent 入口（不直连 tools/）。"""
    calls: list[tuple[str, str, str | None]] = []

    def spy(name: str, phone: str, *, session_id: str) -> SimpleNamespace:
        calls.append((name, phone, session_id))
        return SimpleNamespace(reply="（假 agent 回执）", intent="payee_add", tool_calls=["add_payee"],
                               tier="L1", executed=True, trace_id="trace-spy")

    monkeypatch.setattr(orchestrator, "submit_payee", spy)
    status, body = api_smoke.call(create_app(), "POST", "/api/payee",
                                  {"name": "李四", "phone": FULL, "session_id": "s-1"})
    assert status == 200 and calls == [("李四", FULL, "s-1")]
    assert tuple(body) == RESPONSE_FIELDS and body["reply"] == "（假 agent 回执）"
    assert count(seeded, "payee") == 5                                # 假 agent 不落库（证明没绕开它）


def test_payee_endpoint_reports_bad_input_without_echoing_it(seeded: Path) -> None:
    before = count(seeded, "payee")
    status, body = api_smoke.call(create_app(), "POST", "/api/payee", {"name": "王小明", "phone": "1381234567"})
    assert status == 200                                              # 参数类失败走回执，不是 5xx
    assert tuple(body) == RESPONSE_FIELDS
    assert body["executed"] is False and "1381234567" not in body["reply"]
    assert count(seeded, "payee") == before


def test_chat_endpoint_shape_is_unchanged(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """加 `/api/payee` 不许动 `/api/chat` 的契约：仍然恰好 6 个字段、顺序不变、照旧跑工具。"""
    from agent import classifier, llm

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "balance_query", "confidence": 0.95, "slots": {}})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    status, body = api_smoke.call(create_app(), "POST", "/api/chat", {"text": "查一下余额"})
    assert status == 200 and tuple(body) == RESPONSE_FIELDS
    assert body["intent"] == "balance_query" and "get_balance" in body["tool_calls"]


def test_healthz_lists_both_endpoints() -> None:
    status, body = api_smoke.call(create_app(), "GET", "/healthz")
    assert status == 200 and body["status"] == "ok"
    assert body["endpoints"] == ["/api/chat", "/api/payee"]
