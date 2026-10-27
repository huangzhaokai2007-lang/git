"""F18 惰性触达：订阅提醒随下一次交互带出，每笔每期只提一次。

与 `tests/test_subscription_remind.py` 的分工：那边测**工具**（窗口 / 边界 / 隔离），
这里测**注入层**（去重 / 不抢答 / 故障隔离 / 不污染侧边栏）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from agent import classifier, orchestrator, subscription_notice
from data.seed import AS_OF
from tools import subscription_remind

from tests.conftest import write_sql

SESSION = "sess-f18-notice"


@pytest.fixture(autouse=True)
def _clean_dedupe():
    """去重状态是进程内的，逐用例复位（否则第二个用例看不到提醒）。"""
    subscription_notice.reset()
    yield
    subscription_notice.reset()


def _set_charge(path: Path, sub_id: str, days: int, status: str = "active") -> None:
    charge = (AS_OF + timedelta(days=days)).isoformat()
    write_sql(path, [("UPDATE subscription SET next_charge_date = ?, status = ? WHERE id = ?",
                      (charge, status, sub_id))])


def _only_one_due(path: Path) -> None:
    """只留 `sub_0001` 在窗口内，其余挪远 —— 断言才不会被别的订阅干扰。"""
    _set_charge(path, "sub_0001", 2)
    for sub_id in ("sub_0002", "sub_0003", "sub_0004"):
        _set_charge(path, sub_id, 999)


def _say(text: str, session: str = SESSION, intent: str = "balance_query"):
    return orchestrator.handle(
        text, session_id=session,
        intent_resolver=lambda *_: classifier.IntentOut(
            intent=intent, confidence=1.0, slots={"account_type": "savings"}))


def test_first_turn_carries_the_reminder_and_the_second_does_not(seeded: Path) -> None:
    """核心行为：第一次交互带出提醒，紧接着的第二次不再重复。"""
    _only_one_due(seeded)
    first = _say("查一下余额")
    second = _say("查一下余额")
    assert "【订阅提醒】" in first.reply
    assert "【订阅提醒】" not in second.reply


def test_reminder_names_merchant_date_and_amount(seeded: Path) -> None:
    """提醒必须说清「谁、什么时候、多少钱」—— 数字全部来自 facts。"""
    _only_one_due(seeded)
    item = subscription_remind.list_due_subscriptions().facts["items"][0]
    reply = _say("查一下余额").reply
    assert item["merchant"] in reply
    assert item["next_charge_date"] in reply
    assert item["amount_yuan"] in reply


def test_asking_for_reminders_does_not_stack_a_second_notice(seeded: Path) -> None:
    """本轮用户就是在查订阅 → 不再叠一句提醒（否则同一批数据说两遍）。"""
    _only_one_due(seeded)
    turn = _say("未来 7 天有什么要扣费的订阅", intent="subscription_remind")
    assert "【订阅提醒】" not in turn.reply


def test_sidebar_balance_refresh_is_not_polluted(seeded: Path) -> None:
    """侧边栏余额借用了 handle，但它不是对话 —— 提醒不能混进余额文案。"""
    _only_one_due(seeded)
    turn = _say("查一下余额", session=f"{SESSION}:sidebar-balance")
    assert "【订阅提醒】" not in turn.reply


def test_next_cycle_reminds_again(seeded: Path) -> None:
    """去重键带扣费日：下一期换了日期就应当重新提醒。"""
    _only_one_due(seeded)
    assert "【订阅提醒】" in _say("查一下余额").reply
    _set_charge(seeded, "sub_0001", 5)
    assert "【订阅提醒】" in _say("查一下余额").reply


def test_each_session_gets_its_own_reminder(seeded: Path) -> None:
    """去重按 session 分开：换一个会话应当重新提醒一次。"""
    _only_one_due(seeded)
    assert "【订阅提醒】" in _say("查一下余额", session="sess-a").reply
    assert "【订阅提醒】" in _say("查一下余额", session="sess-b").reply


def test_tool_failure_never_breaks_the_request(seeded: Path, monkeypatch) -> None:
    """取数失败只记日志，本次回执照常返回。"""
    monkeypatch.setattr(subscription_remind, "list_due_subscriptions",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    turn = _say("查一下余额")
    assert turn.reply and "【订阅提醒】" not in turn.reply


def test_nothing_due_means_no_notice(seeded: Path) -> None:
    """窗口内没有要扣的 → 一句都不加（不能出现空提醒）。"""
    for sub_id in ("sub_0001", "sub_0002", "sub_0003", "sub_0004"):
        _set_charge(seeded, sub_id, 999)
    assert "【订阅提醒】" not in _say("查一下余额").reply
