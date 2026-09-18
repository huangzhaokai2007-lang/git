"""卡 10 单测：L1/L2 写操作端到端（确认卡 → OTP → 幂等执行）。

沿用仓库做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）—— 转账走真实数据库，
断言的是余额与流水；假 LLM 只负责给出分类结果与"一个数字都不动"的润色。

卡文四个必测场景（含三个假绿点）：
① 未确认就执行 → executed=False 且余额/流水不变；
② OTP 错误 3 次 → 不执行且锁会话；
③ 同一 preview_token 二次执行 → 只扣一次（流水=1、审计不新增）；
④ 新收款人 + 夜间 → L3，并断言 tier 值与 factors 明细（不是"非 L1"）。

种子事实（`data/seed.py`）：`payee_0001` 李四 139****1001 白名单 / `payee_0002` 李四 139****1002
非白名单（同名 → 歧义用例）/ `payee_0003` 王五 白名单 / `payee_0004` 张小美 非白名单。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator, write_flow
from guard import permission
from tools import transfer

from tests.conftest import OTP, PAYEE, balance, count, raw

SESSION = "sess-card10"
WANGWU, ZHANG = "王五", "张小美"


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def intent(slots: dict, confidence: float = 0.95) -> dict:
    """一条转账意图（槽位名与 classifier 的 SLOT_SCHEMA 一致：payee = 名字、amount = 元）。"""
    return {"intent": "transfer_single", "confidence": confidence, "slots": slots, "missing_slots": []}


@pytest.fixture()
def wired(seeded: Path, monkeypatch: pytest.MonkeyPatch, clock):
    """把确认流程的时钟与会话态一起钉住，并返回合成库路径（每用例从干净状态开始）。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield seeded
    confirm_card.reset_state()


def turn_for(monkeypatch: pytest.MonkeyPatch, text: str, script: list[dict]):
    fake_llm(monkeypatch, script)
    return orchestrator.handle(text, session_id=SESSION)


# ---------------- ① 必测：未确认就执行 → 不执行且分文不动 ----------------

def test_unconfirmed_transfer_is_not_executed_and_moves_no_money(wired, monkeypatch) -> None:
    before_balance, before_txn = balance(wired), count(wired, "txn")
    asked = turn_for(monkeypatch, f"给{WANGWU}转 100 元", [intent({"payee": WANGWU, "amount": "100.00"})])
    assert asked.executed is False and asked.tier in ("L1", "L2")
    assert "CONFIRM_CARD" in asked.states and "【转账确认卡】" in asked.reply
    assert "137****2003" in asked.reply                                     # 脱敏手机号（铁律 8）
    assert confirm_card.current(SESSION) is not None                        # 停在等确认
    assert balance(wired) == before_balance and count(wired, "txn") == before_txn


def test_reply_other_than_confirmation_returns_to_slot_fill(wired, monkeypatch) -> None:
    before_balance, before_txn = balance(wired), count(wired, "txn")
    turn_for(monkeypatch, f"给{WANGWU}转 100 元", [intent({"payee": WANGWU, "amount": "100.00"})])
    follow = orchestrator.handle("算了，改一下金额", session_id=SESSION)      # 在途确认优先，不再分类
    assert "SLOT_FILL" in follow.states and follow.executed is False
    assert confirm_card.current(SESSION) is None                            # 本笔作废
    assert balance(wired) == before_balance and count(wired, "txn") == before_txn


# ---------------- ② 必测：OTP 错误 3 次 → 不执行 + 锁会话 ----------------

def test_wrong_otp_three_times_locks_session_and_never_executes(wired, monkeypatch) -> None:
    before_balance = balance(wired)
    card = turn_for(monkeypatch, f"给{ZHANG}转 100 元", [intent({"payee": ZHANG, "amount": "100.00"})])
    assert card.tier == "L2" and card.executed is False                     # 非白名单收款人 → L2 基础档
    assert "验证码" in card.reply                                           # L2 要 OTP（双因子）
    orchestrator.handle("确认", session_id=SESSION)                          # → 等 OTP
    for attempt in (1, 2):
        wrong = orchestrator.handle("000000", session_id=SESSION)
        assert wrong.executed is False and str(3 - attempt) in wrong.reply   # 剩余次数来自代码计数
    locked = orchestrator.handle("000000", session_id=SESSION)
    assert locked.executed is False and "锁定" in locked.reply
    assert confirm_card.is_locked(SESSION) is True and balance(wired) == before_balance
    blocked = turn_for(monkeypatch, f"给{WANGWU}转 10 元", [intent({"payee": WANGWU, "amount": "10.00"})])
    assert blocked.executed is False and "锁定" in blocked.reply             # 锁住后新单也拒绝


def test_correct_otp_executes_exactly_once(wired, monkeypatch) -> None:
    before_balance, before_txn = balance(wired), count(wired, "txn")
    turn_for(monkeypatch, f"给{ZHANG}转 100 元", [intent({"payee": ZHANG, "amount": "100.00"})])
    prompted = orchestrator.handle("确认", session_id=SESSION)
    assert prompted.executed is False and "验证码" in prompted.reply
    done = orchestrator.handle(OTP, session_id=SESSION)
    assert done.executed is True and "VERIFY_NUMBERS" in done.states
    assert balance(wired) == before_balance - 10_000                        # 100.00 元 = 10000 分
    assert count(wired, "txn") == before_txn + 1
    assert confirm_card.current(SESSION) is None                            # 确认一次性消费


# ---------------- ③ 必测：同一 preview_token 二次执行 → 只扣一次 ----------------

def test_same_preview_token_executes_only_once(wired) -> None:
    preview = transfer.preview_transfer(PAYEE, 10_000)
    assert preview.ok, preview.message
    token = preview.data["preview_token"]
    before_balance, before_txn = balance(wired), count(wired, "txn")
    before_audit = count(wired, "audit_log")
    first = transfer.execute_transfer(token, OTP)
    assert first.ok and balance(wired) == before_balance - 10_000
    again = transfer.execute_transfer(token, OTP)                           # 幂等：同 token 同结果
    assert again.ok and again.data == first.data
    assert balance(wired) == before_balance - 10_000                        # 只扣一次
    assert count(wired, "txn") == before_txn + 1 and count(wired, "audit_log") == before_audit + 1


# ---------------- ④ 必测：新收款人 + 夜间 → L3（tier 值 + factors 明细） ----------------

def test_new_payee_at_night_escalates_to_l3_with_explicit_factors(wired, clock, monkeypatch) -> None:
    clock.moment = clock.moment.replace(hour=23, minute=30)                  # 夜间（23:00-06:00）
    before_balance = balance(wired)
    turn = turn_for(monkeypatch, f"给{ZHANG}转 100 元", [intent({"payee": ZHANG, "amount": "100.00"})])
    assert turn.tier == "L3" and turn.to_human is True and turn.executed is False
    assert "PENDING_REVIEW" in turn.states and turn.pending_id is not None
    assert "撤销" in turn.reply and turn.pending_id in turn.reply            # 撤销入口
    assert balance(wired) == before_balance                                 # L3 不执行
    facts = transfer.preview_transfer(PAYEE, 10_000).facts
    assert "night" in facts["factors"]                                      # 同一套 §5 内核的因子明细
    assert "夜间" in turn.reply                                              # 卡上如实提示因子


# ---------------- 附图：档位、金额、歧义、脱敏、L3 撤销 ----------------

def test_guard_escalation_rules_are_pure_code() -> None:
    l2 = permission.assess_write("transfer_single", tier="L2", factors=["night"])
    assert (l2.tier, l2.requires_otp, l2.delayed, l2.to_human) == ("L2", True, False, False)
    l3 = permission.assess_write("transfer_single", tier="L3", factors=["night", "velocity"])
    assert (l3.tier, l3.to_human, l3.delayed, l3.requires_otp) == ("L3", True, True, True)
    assert permission.assess_write("card_report_lost").tier == "L3"         # §5 表：挂失 = L3
    assert permission.assess_write("wealth_buy").tier == "L2"               # §5 表：申购赎回 = L2
    fallback = permission.assess_write("some_unknown_write")                # 未登记 → fail-closed
    assert (fallback.tier, fallback.to_human, fallback.source) == ("L3", True, "fail_closed")


def test_yuan_to_cents_uses_integer_math() -> None:
    assert write_flow.yuan_to_cents("100.00") == 10_000
    assert write_flow.yuan_to_cents("0.1") == 10                          # 0.1 元的坑：禁 float
    assert write_flow.yuan_to_cents("1,234.5") == 123_450
    assert write_flow.yuan_to_cents("1.235") is None and write_flow.yuan_to_cents("abc") is None
    assert write_flow.yuan_to_cents("0") is None                          # 非正数 → 缺槽


def test_missing_amount_is_clarified(wired, monkeypatch) -> None:
    turn = turn_for(monkeypatch, f"给{WANGWU}转点钱", [intent({"payee": WANGWU})])
    assert turn.executed is False and turn.missing_slots == ["amount"] and turn.ask is not None


def test_ambiguous_payee_is_never_auto_picked(wired, monkeypatch) -> None:
    before_balance = balance(wired)
    turn = turn_for(monkeypatch, "给李四转 100 元", [intent({"payee": "李四", "amount": "100.00"})])
    assert turn.executed is False and turn.missing_slots == ["payee"]       # 同名两位 → 追问
    assert balance(wired) == before_balance and confirm_card.current(SESSION) is None


def test_confirmation_card_numbers_all_come_from_facts(wired, monkeypatch) -> None:
    turn = turn_for(monkeypatch, f"给{WANGWU}转 100 元", [intent({"payee": WANGWU, "amount": "100.00"})])
    payee = transfer.resolve_payee(WANGWU).facts["candidates"][0]           # 收款人事实（含脱敏手机号）
    facts = {**transfer.preview_transfer(payee["id"], 10_000).facts, "payee_phone": payee["phone"]}
    assert payee["phone"] in turn.reply                                     # 脱敏号来自工具事实包
    assert write_flow.card_numbers_outside_facts(turn.reply, facts) == []  # 卡上数字全部可溯


def test_l3_pending_can_be_revoked_before_it_takes_effect(wired, clock, monkeypatch) -> None:
    clock.moment = clock.moment.replace(hour=23, minute=30)
    before_balance = balance(wired)
    turn = turn_for(monkeypatch, f"给{ZHANG}转 100 元", [intent({"payee": ZHANG, "amount": "100.00"})])
    assert confirm_card.revoke(turn.pending_id, "别人的会话") is False        # 别人的编号撤销不了
    assert confirm_card.revoke(turn.pending_id, SESSION) is True
    assert confirm_card.due(turn.pending_id, confirm_card.now_iso()) is False  # 撤销后永不生效
    assert balance(wired) == before_balance
    assert raw(wired, "SELECT COUNT(*) AS n FROM txn WHERE memo LIKE '%李四%'")[0]["n"] >= 0
