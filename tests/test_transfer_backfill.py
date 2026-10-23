"""转账执行的回填守卫：`txn.payee_id` + `payee.last_used_ts`（设计 §2「由执行时回填」）。

缺口：v2 加了 `txn.payee_id` 列、`payee.last_used_ts` 也早就存在，但执行转账时
两者都不回填 —— 额度只能按名字聚合（同名混淆）、「首次收款」因子永远挂着。
"""

from __future__ import annotations

import json

import pytest

from agent import confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw

UID = "u_zhangsan_0001"
SESSION = "sess-backfill"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, amount: str = "100") -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "transfer_single", "confidence": 0.95,
                                          "slots": {"payee": "房东", "amount": amount},
                                          "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_execute_backfills_payee_id_and_last_used(wired, monkeypatch) -> None:
    """执行成功后：新流水带 `payee_id`、收款人的 `last_used_ts` 刷新（白天 → 王五 L1 直接执行）。"""
    _fake(monkeypatch)
    wang = raw(wired, "SELECT id, last_used_ts FROM payee WHERE name = ?", (WANGWU,))[0]
    before = wang["last_used_ts"]
    card = orchestrator.handle("给房东转 100 元", session_id=SESSION)
    assert card.tier == "L1"                                            # 白天 + 白名单额度内
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    txn = raw(wired, "SELECT payee_id FROM txn ORDER BY ts DESC LIMIT 1")[0]
    assert txn["payee_id"] == wang["id"]                                # txn 回填收款人 id
    after = raw(wired, "SELECT last_used_ts FROM payee WHERE id = ?", (wang["id"],))[0]
    assert after["last_used_ts"] == "2026-09-12T12:00:00"               # 时钟钉住的执行时刻
    assert after["last_used_ts"] != before


def test_quota_aggregates_by_payee_id_after_backfill(wired, monkeypatch) -> None:
    """回填后额度按 **id** 聚合（不再是名字兜底），剩余额度精确扣减。"""
    from data import payee_dao
    _fake(monkeypatch, amount="30")
    wang = raw(wired, "SELECT id FROM payee WHERE name = ?", (WANGWU,))[0]
    payee_dao.update_payee(wang["id"], UID, whitelist_quota=10_000)     # 100 元额度
    month = "2026-09"                     # 与 clock 夹具钉住的时刻同月（额度按流水所在月聚合）
    person = {"id": wang["id"], "whitelist_quota": 10_000}
    remaining_before = payee_dao.remaining_quota_cents(person, month)
    orchestrator.handle("给房东转 30 元", session_id=SESSION)
    assert orchestrator.handle("确认", session_id=SESSION).executed is True
    remaining_after = payee_dao.remaining_quota_cents(person, month)
    assert remaining_after == remaining_before - 3_000                  # 扣 30 元
