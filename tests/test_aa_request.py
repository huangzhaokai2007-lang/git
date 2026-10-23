"""F22 追加：**AA 进行中 / 每人已付 / 24h 失效 / 撤销**。

| 验收点 | 用例 |
|---|---|
| 发起 → `active` + 每人一份 + 24h 截止 | `test_open_request_creates_shares_and_deadline` |
| 每人已付：登记 → 份额 paid；全部付清 → done | `test_mark_paid_and_complete` |
| 重复登记 / 撤销后登记 → INVALID_STATE | `test_mark_paid_rejects_repeat_and_cancelled` |
| **24h 失效**：过点后读请求显示 expired、登记被拒 | `test_expired_after_24_hours` |
| 撤销：`active → cancelled`，已付记录保留 | `test_cancel_keeps_paid_records` |
| 编排层：AA 收款 → 单号 → 「赵六付了」→ 「我的 AA」表格 | `test_orchestration_aa_lifecycle` |
"""

from __future__ import annotations

import datetime
import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import _aa_request as aa

SESSION = "sess-aa"
TITLE = "周末聚餐"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _payee_ids(wired, names: list[str]) -> list[str]:
    return [raw(wired, "SELECT id FROM payee WHERE name = ?", (n,))[0]["id"] for n in names]


def test_open_request_creates_shares_and_deadline(wired) -> None:
    ids = _payee_ids(wired, ["王五", "赵六"])
    result = aa.open_aa_request(ids, [15_000, 15_000], 30_000, "equal", TITLE)
    assert result.ok is True and result.data["request_id"].startswith("aa_")
    row = raw(wired, "SELECT status, expire_at, title FROM aa_request")[0]
    assert row["status"] == "active" and row["title"] == TITLE
    deadline = datetime.datetime.fromisoformat(row["expire_at"])
    assert 23 <= (deadline - datetime.datetime.now()).total_seconds() / 3600 <= 24.1
    assert len(raw(wired, "SELECT 1 FROM aa_share")) == 2
    assert aa.list_aa_requests().facts["items"][0]["status_cn"] == "进行中"


def test_mark_paid_and_complete(wired) -> None:
    ids = _payee_ids(wired, ["王五", "赵六"])
    rid = aa.open_aa_request(ids, [15_000, 15_000], 30_000, "equal", TITLE).data["request_id"]
    first = aa.mark_aa_share_paid(rid, ids[0])
    assert first.ok is True and first.data["status"] == "active"          # 还有一人没付
    item = aa.list_aa_requests().facts["items"][0]
    assert item["paid_count"] == 1 and item["share_count"] == 2
    second = aa.mark_aa_share_paid(rid, ids[1])
    assert second.data["status"] == "done"                                # 全部付清
    assert raw(wired, "SELECT status FROM aa_request")[0]["status"] == "done"


def test_mark_paid_rejects_repeat_and_cancelled(wired) -> None:
    ids = _payee_ids(wired, ["王五", "赵六"])
    rid = aa.open_aa_request(ids, [15_000, 15_000], 30_000, "equal", TITLE).data["request_id"]
    assert aa.mark_aa_share_paid(rid, ids[0]).ok is True
    again = aa.mark_aa_share_paid(rid, ids[0])
    assert again.ok is False and "已经登记过" in again.message
    assert aa.cancel_aa_request(rid).ok is True
    assert aa.mark_aa_share_paid(rid, ids[1]).ok is False                  # 已撤销 → 不能登记


def test_expired_after_24_hours(wired, monkeypatch) -> None:
    """过 24h：读请求显示「已失效」，登记付款被拒（**读路径不写库**）。"""
    ids = _payee_ids(wired, ["王五", "赵六"])
    rid = aa.open_aa_request(ids, [15_000, 15_000], 30_000, "equal", TITLE).data["request_id"]
    later = datetime.datetime.now() + datetime.timedelta(hours=25)
    monkeypatch.setattr(aa, "_now", lambda: later)
    item = aa.list_aa_requests().facts["items"][0]
    assert item["status"] == "expired" and item["status_cn"] == "已失效"
    assert raw(wired, "SELECT status FROM aa_request")[0]["status"] == "active"   # 库里没被改写
    blocked = aa.mark_aa_share_paid(rid, ids[0])
    assert blocked.ok is False and "超过 24 小时" in blocked.message


def test_cancel_keeps_paid_records(wired) -> None:
    ids = _payee_ids(wired, ["王五", "赵六"])
    rid = aa.open_aa_request(ids, [15_000, 15_000], 30_000, "equal", TITLE).data["request_id"]
    assert aa.mark_aa_share_paid(rid, ids[0]).ok is True
    result = aa.cancel_aa_request(rid)
    assert result.ok is True and result.facts["paid_count"] == 1
    assert raw(wired, "SELECT status FROM aa_request")[0]["status"] == "cancelled"
    paid = raw(wired, "SELECT COUNT(*) AS n FROM aa_share WHERE status = 'paid'")[0]["n"]
    assert paid == 1                                                      # 已付记录保留


def test_orchestration_aa_lifecycle(wired, monkeypatch) -> None:
    """发起 AA → 单号；「赵六付了」→ 登记；「我的 AA」→ 表格。"""
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["王五", "赵六"], "amount": "300"},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)
    card = orchestrator.handle("向王五和赵六 AA 收款 300 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states
    stage = orchestrator.handle("确认", session_id=SESSION)      # 含非白名单 → 先要验证码
    assert stage.executed is False and "验证码" in stage.reply
    done = orchestrator.handle("123456", session_id=SESSION)
    assert done.executed is True and "AA 单号 aa_" in done.reply

    def chat_json2(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "aa_mark_paid", "confidence": 0.95,
                                          "slots": {"payee": "赵六"}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json2)
    marked = orchestrator.handle("赵六付了", session_id=SESSION)
    assert "CONFIRM_CARD" in marked.states and marked.tier == "L1"
    assert "登记已付确认卡" in marked.reply
    ok = orchestrator.handle("确认", session_id=SESSION)
    assert ok.executed is True and ok.tool_calls == ["mark_aa_share_paid"]

    def chat_json3(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "aa_list", "confidence": 0.95,
                                          "slots": {}, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json3)
    listed = orchestrator.handle("我的 AA", session_id=SESSION)
    assert listed.intent == "aa_list" and "list_aa_requests" in listed.tool_calls
    assert "| 事由 | 合计（元） | 已付 | 状态 | 截止 |" in listed.reply
    assert "1/2 人" in listed.reply and "进行中" in listed.reply
