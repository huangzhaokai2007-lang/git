"""card-24 单测：订阅取消（F04）端到端 —— 编排层路由 + 泛化写路径的 `confirm_ref` 分支。

沿用仓库做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）—— 订阅取消走真实数据库，
断言的是订阅状态与回执；假 LLM 只负责给出分类结果与「一个数字都不动」的润色。

**本文件只测编排层特有行为**（工具层自己的 OTP / 幂等 / 归属已在 `test_tools_subscription_cancel.py`）：

① 唯一商户命中才补 `sub_id`；重名 → 追问；零命中 → 报错（F04 验收清单第 1 条）；
② 未确认不取消（订阅状态不变）；
③ 确认 + OTP → 执行成功；
④ **OTP 闸门在编排层**（T11 的签名没有 otp 参数）→ 错误 OTP 被本层拦下；
⑤ 错满 3 次锁会话；
⑥ 卡面数字全部可溯（未降级）。

种子事实（`data/seed.py`）：`sub_0001` 云音乐 15.00 元／月、下次扣费 2026-09-18、active。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator
from data.seed import USER_ID

from tests.conftest import OTP, raw, write_sql

SESSION = "sess-f04"
MERCHANT = "云音乐"          # sub_0001：active、15.00 元／月、下次扣费 2026-09-18
SUB_ID = "sub_0001"


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def cancel_intent(slots: dict) -> dict:
    """一条订阅取消意图（槽位名与 `classifier.SLOT_SCHEMA` 一致）。"""
    return {"intent": "subscription_cancel", "confidence": 0.95, "slots": slots,
            "missing_slots": []}


def say(monkeypatch: pytest.MonkeyPatch, text: str, script: list[dict] | None = None):
    """驱动一句输入（在途确认 / OTP 阶段不调 LLM，故 script 可省）。"""
    if script is not None:
        fake_llm(monkeypatch, script)
    return orchestrator.handle(text, session_id=SESSION)


@pytest.fixture()
def wired(seeded: Path, monkeypatch: pytest.MonkeyPatch, clock):
    """钉住确认流程的时钟与会话态，返回合成库路径（每用例从干净状态开始）。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield seeded
    confirm_card.reset_state()


@pytest.fixture()
def duplicated(seeded: Path) -> Path:
    """再插一个同名商户的订阅（与 `sub_0001` 同为「云音乐」）→ 重名歧义用例。"""
    write_sql(seeded, [
        ("INSERT INTO subscription (id, user_id, merchant, amount, cycle, next_charge_date, status)"
         " VALUES (?, ?, ?, ?, 'monthly', ?, 'active')",
         ("sub_dup_0001", USER_ID, MERCHANT, 1_500, "2026-09-20"))])
    return seeded


def sub_status(path: Path, sub_id: str = SUB_ID) -> str:
    return raw(path, "SELECT status FROM subscription WHERE id = ?", (sub_id,))[0]["status"]


# ---------------- ① 商户解析：唯一命中 / 重名 / 零命中 ----------------

def test_unique_merchant_fills_sub_id_and_shows_card(wired, monkeypatch) -> None:
    """唯一命中 → 补 sub_id、出 L2 确认卡；**此时还没执行**。"""
    turn = say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    assert turn.intent == "subscription_cancel"
    assert turn.tier == "L2" and turn.executed is False
    assert "SLOT_FILL" in turn.states and "PRECHECK" in turn.states and "CONFIRM_CARD" in turn.states
    assert "订阅取消确认卡" in turn.reply and MERCHANT in turn.reply
    inflight = confirm_card.current(SESSION)
    assert inflight is not None and inflight.requires_otp is True
    assert sub_status(wired) == "active"                       # 未确认 → 分文未动


def test_ambiguous_merchant_asks_instead_of_guessing(wired, duplicated, monkeypatch) -> None:
    """重名 → CLARIFY 追问，**绝不擅自选一个**。"""
    turn = say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    assert turn.executed is False
    assert turn.missing_slots == ["merchant"] and "CLARIFY" in turn.states
    assert confirm_card.current(SESSION) is None               # 没进确认流程
    assert sub_status(duplicated) == "active"


def test_unknown_merchant_reports_not_found(wired, monkeypatch) -> None:
    """零命中 → 报错（F04 验收点，与转账「一律追问」的口径不同）。"""
    turn = say(monkeypatch, "取消这个订阅", [cancel_intent({"merchant": "没有这个商户"})])
    assert turn.executed is False
    assert turn.error_code == "NOT_FOUND"
    assert confirm_card.current(SESSION) is None
    assert sub_status(wired) == "active"


# ---------------- ② 未确认不执行 ----------------

def test_non_confirmation_aborts_without_cancelling(wired, monkeypatch) -> None:
    """确认阶段回复非认可词 → 本笔作废，订阅不动。"""
    say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    aborted = say(monkeypatch, "算了不要了")
    assert aborted.executed is False
    assert confirm_card.current(SESSION) is None
    assert sub_status(wired) == "active"


# ---------------- ③④⑤ 确认 + OTP（闸门在编排层） ----------------

def test_confirm_then_otp_cancels_subscription(wired, monkeypatch) -> None:
    """确认 → 等 OTP → 正确验证码 → 订阅翻成 cancelled。"""
    say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    waiting = say(monkeypatch, "确认")
    assert waiting.executed is False and "验证码" in waiting.reply
    done = say(monkeypatch, OTP)
    assert done.executed is True and done.tier == "L2"
    assert "VERIFY_NUMBERS" in done.states
    assert MERCHANT in done.reply
    assert sub_status(wired) == "cancelled"


def test_wrong_otp_is_rejected_by_orchestrator(wired, monkeypatch) -> None:
    """T11 的 OTP 闸门在编排层：错误验证码由本层拦下，工具层根本不会被调用。"""
    say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    say(monkeypatch, "确认")
    bad = say(monkeypatch, "000000")
    assert bad.executed is False
    assert "验证码不正确" in bad.reply
    assert sub_status(wired) == "active"


def test_otp_locked_after_three_errors(wired, monkeypatch) -> None:
    """错满 3 次锁会话（卡 10 第 4 条口径，订阅侧同样生效）。"""
    say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    say(monkeypatch, "确认")
    for _ in range(2):
        assert "验证码不正确" in say(monkeypatch, "000000").reply
    locked = say(monkeypatch, "000000")
    assert "锁定" in locked.reply
    assert confirm_card.is_locked(SESSION) is True
    assert sub_status(wired) == "active"


# ---------------- ⑥ 卡面数字可溯 ----------------

def test_confirmation_card_numbers_all_come_from_facts(wired, monkeypatch) -> None:
    """卡面每个数字都要能在事实包里找到；一旦出现表外数字就降级（`degraded=True`）。"""
    turn = say(monkeypatch, "取消云音乐订阅", [cancel_intent({"merchant": MERCHANT})])
    assert turn.degraded is False                              # 未降级 = 卡面数字全部可溯
    assert "【订阅取消确认卡】" in turn.reply
    assert "15.00" in turn.reply                               # 金额来自 T10 事实包
    assert "2026-09-18" in turn.reply                          # 下次扣费日同样来自事实包
    assert "L2" in turn.reply                                  # 档位由 guard 纯代码判
