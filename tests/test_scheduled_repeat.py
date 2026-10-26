"""F16 追加：**循环预约**（T34 设周期 / T35 续下一期）。

| 验收点 | 用例 |
|---|---|
| 「每个月月末」→ monthly（确定性映射） | `test_repeat_words_normalize` |
| T34：pending 可设周期；非法周期拒绝 | `test_set_repeat_updates_pending` |
| T35：结算后生成下一期；**月末对齐**（1/31 → 2/28） | `test_next_occurrence_month_end_alignment` |
| T35：非循环 / 上期未成功 → 幂等无操作 | `test_next_occurrence_noop_for_non_repeat` |
| 编排层：一句话带「每月」→ 卡面有「循环」→ 确认 → 库里记 monthly | `test_orchestration_monthly_schedule` |
"""

from __future__ import annotations

import json

import pytest

from agent import _write_transfer, confirm_card, llm, orchestrator, session_memory
from tests.conftest import raw
from tools import _transfer_schedule_ops as ops

SESSION = "sess-repeat"
WANGWU = "王五"


@pytest.fixture()
def wired(seeded, monkeypatch, clock):
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    session_memory.reset()
    yield seeded
    confirm_card.reset_state()
    session_memory.reset()


def _fake(monkeypatch, slots: dict) -> None:
    def chat_json(system: str, user: object, schema):
        if getattr(schema, "__name__", "") == "IntentOut":
            return schema.model_validate({"intent": "transfer_scheduled", "confidence": 0.95,
                                          "slots": slots, "missing_slots": []})
        return schema(reply=json.loads(user)["reply"])

    monkeypatch.setattr(llm, "chat_json", chat_json)


def test_repeat_words_normalize() -> None:
    """「每个月月末 / 每月 / 每周」→ 周期码；认不出 → None（按一次性，不打扰用户）。"""
    from agent._write_common import repeat_cycle
    assert repeat_cycle("每个月月末") == "monthly"
    assert repeat_cycle("每月") == "monthly"
    assert repeat_cycle("每周") == "weekly"
    assert repeat_cycle("就这一次") is None
    assert repeat_cycle(None) is None


# ---------------- 相对时间 + 循环不追问日期（实测缺陷） ----------------

def test_relative_time_parser() -> None:
    """明天/下周一/下个月5号/月底/时刻 → ISO（确定性，不读系统钟）。"""
    import datetime
    from agent import _relative_time as rt
    now = datetime.datetime(2026, 10, 23, 12, 0)               # 周五
    assert rt.parse("明天", now) == "2026-10-24T09:00"
    assert rt.parse("后天 晚上 8 点", now) == "2026-10-25T20:00"
    assert rt.parse("下周一", now) == "2026-10-26T09:00"
    assert rt.parse("下个月 5 号", now) == "2026-11-05T09:00"
    assert rt.parse("月底", now) == "2026-10-31T09:00"
    assert rt.parse("瞎说的", now) is None
    assert rt.next_month_end(now).isoformat() == "2026-10-31"


def test_iso_schedule_accepts_relative(wired) -> None:
    """`_iso_schedule` 先认 ISO、再认相对说法（以前只认 ISO）。"""
    from agent._write_transfer import _iso_schedule
    assert _iso_schedule("2026-11-01 09:00") == "2026-11-01T09:00"
    assert _iso_schedule("明天") is not None                  # 钉住的测试钟 +1 天
    assert _iso_schedule("胡言乱语") is None


def test_monthly_without_date_does_not_ask(wired, monkeypatch) -> None:
    """「每个月月末给房东转 300」**不给日期** → 自动推首期（最近月末），不再追问格式。"""
    _fake(monkeypatch, {"payee": "房东", "amount": "300", "repeat": "每个月月末"})
    card = orchestrator.handle("每个月月末给房东转 300 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states, card.reply
    assert "预约时间" in card.reply and "循环" in card.reply
    assert "请说明预约时间" not in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True
    row = raw(wired, "SELECT execute_at, repeat_cycle FROM scheduled_transfer WHERE status = 'pending'")[0]
    assert row["repeat_cycle"] == "monthly"
    assert row["execute_at"].endswith("T09:00")               # 缺省 09:00


def _make_schedule(wired, monkeypatch, when: str = "2026-10-31 09:00", repeat: str = "") -> str:
    slots = {"payee": WANGWU, "amount": "100", "schedule": when}
    if repeat:
        slots["repeat"] = repeat
    _fake(monkeypatch, slots)
    orchestrator.handle(f"{when} 给{WANGWU}转 100 元", session_id=SESSION)
    assert orchestrator.handle("确认", session_id=SESSION).executed is True
    return raw(wired, "SELECT id FROM scheduled_transfer WHERE status = 'pending' ORDER BY rowid DESC LIMIT 1")[0]["id"]


def test_set_repeat_updates_pending(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)
    assert ops.set_schedule_repeat(sid, "monthly").ok is True
    assert raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE id = ?", (sid,))[0]["repeat_cycle"] == "monthly"
    assert ops.set_schedule_repeat(sid, "yearly").ok is False          # 只支持每月/每周


def test_next_occurrence_month_end_alignment(wired, monkeypatch) -> None:
    """1 月 31 日的 monthly → 下一期 2 月 28 日（月末对齐，不漏期）。"""
    assert ops._next_occurrence("2027-01-31T09:00", "monthly") == "2027-02-28T09:00"
    assert ops._next_occurrence("2026-10-31T09:00", "monthly") == "2026-11-30T09:00"
    assert ops._next_occurrence("2026-12-15T09:00", "monthly") == "2027-01-15T09:00"
    assert ops._next_occurrence("2026-10-31T09:00", "weekly") == "2026-11-07T09:00"


def test_next_occurrence_creates_next_pending(wired, monkeypatch) -> None:
    """结算成功后：T35 生成下一期 pending（同收款人/金额/档位，时间 = 下一月同日）。"""
    sid = _make_schedule(wired, monkeypatch, repeat="每个月")
    assert raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE id = ?", (sid,))[0]["repeat_cycle"] == "monthly"
    assert ops.run_scheduled_now(sid).ok is True                       # 首期立即结算
    result = ops.schedule_next_occurrence(sid)
    assert result.ok is True and result.data["next_id"]
    nxt = raw(wired, "SELECT * FROM scheduled_transfer WHERE id = ?", (result.data["next_id"],))[0]
    assert nxt["status"] == "pending" and nxt["repeat_cycle"] == "monthly"
    assert nxt["execute_at"].startswith("2026-11-30")                  # 10-31 → 11-30（月末对齐）
    again = ops.schedule_next_occurrence(sid)                          # 幂等：不重复插
    assert again.data["next_id"] == nxt["id"]


def test_next_occurrence_noop_for_non_repeat(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch)                           # 不带循环
    assert ops.run_scheduled_now(sid).ok is True
    result = ops.schedule_next_occurrence(sid)
    assert result.ok is True and result.data["next_id"] is None
    assert "非循环预约" in result.message


def test_orchestration_monthly_schedule(wired, monkeypatch) -> None:
    """「每个月月末给房东转 100」→ 卡面有「循环」行 → 确认后库里记 monthly。"""
    _fake(monkeypatch, {"payee": "房东", "amount": "100", "schedule": "2026-10-31 09:00",
                        "repeat": "每个月月末"})
    card = orchestrator.handle("每个月月末给房东转 100 元", session_id=SESSION)
    assert "CONFIRM_CARD" in card.states and "循环" in card.reply and "每月循环" in card.reply
    done = orchestrator.handle("确认", session_id=SESSION)
    assert done.executed is True and "每月循环" in done.reply
    row = raw(wired, "SELECT repeat_cycle FROM scheduled_transfer WHERE status = 'pending'")[0]
    assert row["repeat_cycle"] == "monthly"


# ---------------- 2026-10-24 人类规格：周期结束条件 ----------------

def test_repeat_until_stops_the_series(wired, monkeypatch) -> None:
    """设了结束日：越过结束日就不再续期（T35 返回 next_id=None）。"""
    sid = _make_schedule(wired, monkeypatch, repeat="每个月")
    assert ops.set_schedule_repeat(sid, "monthly", "2026-11-15").ok is True
    assert raw(wired, "SELECT repeat_until FROM scheduled_transfer WHERE id = ?", (sid,))[0][
        "repeat_until"] == "2026-11-15"
    assert ops.run_scheduled_now(sid).ok is True
    nxt = ops.schedule_next_occurrence(sid)
    assert nxt.ok is True and nxt.data["next_id"] is None
    assert "结束日" in nxt.message


def test_repeat_without_until_keeps_going(wired, monkeypatch) -> None:
    """没设结束日：照旧续期（回归保护）。"""
    sid = _make_schedule(wired, monkeypatch, repeat="每个月")
    assert ops.run_scheduled_now(sid).ok is True
    nxt = ops.schedule_next_occurrence(sid)
    assert nxt.data["next_id"] is not None


def test_repeat_until_bad_date_rejected(wired, monkeypatch) -> None:
    sid = _make_schedule(wired, monkeypatch, repeat="每个月")
    assert ops.set_schedule_repeat(sid, "monthly", "下个月三十二号").ok is False


# ---------------- 2026-10-26：循环续期的两个实测坑 ----------------

def test_next_occurrence_keeps_month_end_alignment(seeded) -> None:
    """「每个月末」不能漂移：10-31 → 11-30 → **12-31**（此前会变成 12-30）。"""
    from tools._transfer_schedule_ops import _next_occurrence

    assert _next_occurrence("2026-10-31T09:00", "monthly") == "2026-11-30T09:00"
    assert _next_occurrence("2026-11-30T09:00", "monthly") == "2026-12-31T09:00"
    assert _next_occurrence("2026-01-31T09:00", "monthly") == "2026-02-28T09:00"   # 2 月夹紧
    assert _next_occurrence("2026-10-15T09:00", "monthly") == "2026-11-15T09:00"   # 非月末按同日


def test_next_occurrence_weekly_is_plus_seven(seeded) -> None:
    from tools._transfer_schedule_ops import _next_occurrence

    assert _next_occurrence("2026-10-31T09:00", "weekly") == "2026-11-07T09:00"


def test_next_period_keeps_the_end_date(seeded, clock) -> None:
    """续出来的下一期**必须带结束日**，否则「循环到年底」在首期之后就失效。"""
    from data import dao
    from tools import _transfer_schedule, _transfer_schedule_ops

    made = _transfer_schedule.schedule_transfer(
        _preview_for_repeat(), None) if False else None
    del made
    # 直接用工具造一条已到期、带结束日的循环预约，再走续期
    row_id = "sch_test_repeat"
    dao.connection().execute(
        "INSERT INTO scheduled_transfer (id, user_id, payee_id, amount, execute_at, status,"
        " tier, token, created_at, repeat_cycle, repeat_until)"
        " VALUES (?, 'u_zhangsan_0001', 'payee_0003', 50000, '2026-09-30T09:00', 'success',"
        " 'L1', NULL, '2026-09-01T09:00', 'monthly', '2026-12-31')", (row_id,))
    dao.connection().commit()

    out = _transfer_schedule_ops.schedule_next_occurrence(row_id)
    assert out.ok is True and out.data.get("next_id"), out.message
    nxt = dao.connection().execute(
        "SELECT execute_at, repeat_cycle, repeat_until FROM scheduled_transfer WHERE id = ?",
        (out.data["next_id"],)).fetchone()
    assert nxt["execute_at"] == "2026-10-31T09:00"        # 月末对齐
    assert nxt["repeat_cycle"] == "monthly"
    assert nxt["repeat_until"] == "2026-12-31"            # ← 结束日必须继承
