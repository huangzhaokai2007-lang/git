"""F20 单测：送礼计划（T16 + T19）端到端 —— 确认前不锁款 / 只扣可用余额 / 去掉猜日期。

沿用仓库做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）。

F20 验收清单逐条落成用例：

| 验收点 | 用例 |
|---|---|
| 确认与 OTP 前**不锁定资金**、不生成模拟预订 | `test_confirmation_does_not_lock_funds` |
| 说明 `available` 与实际支出的区别 | `test_execute_locks_available_but_never_touches_balance` |
| 重复提交不重复锁款 | `test_second_confirm_does_not_lock_twice` |
| 失败回滚 | `test_budget_below_cheapest_gift_is_rejected` |
| 用户可见数字来自工具事实包 | 卡面数字全部来自 T19 事实包（`_write_gift` 注释 + yaml gft-001） |
| 缺日期不猜 | `test_missing_date_asks_instead_of_guessing` |

**断言 `balance` 与 `available` 必须分开看**：验收第 2 条要求说清「锁定 ≠ 支出」——
所以执行后 `available` 减少、`balance` **一分不动**，这条是本文件的核心断言。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator

from tests.conftest import OTP, raw

SESSION = "sess-f20"
CONTACT = "张三"
DAY = "2026-10-20"          # 晚于基准日 `data.seed.AS_OF`（2026-09-12）
BUDGET = "500.00"           # = 50000 分：买得起鲜花(19900) + 蛋糕(29900) = 49800 分
TOTAL_CENTS = 49_800


def account(path: Path) -> dict:
    """储蓄账户的 `(balance, available)` —— 锁定资金的断言必须两者分开看。"""
    return raw(path, "SELECT balance, available FROM account WHERE type = 'savings'"
                     " ORDER BY id LIMIT 1")[0]


def plan_audits(path: Path) -> int:
    """T16 **成功锁定**的审计条数 —— 唯一能证明「真锁过款」的信号。

    必须过滤 `result='success'`：`write_flow._otp_rejected` 会把**被拒绝的 OTP 尝试**也记一条
    审计（铁律 5 要求留痕，这是对的），但它显然不代表锁过款 —— 不过滤就会把「拒绝」误读成「锁款」。
    """
    return len(raw(path, "SELECT 1 FROM audit_log WHERE tool = ? AND result = 'success'",
                   ("plan_gift",)))


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def gift_intent(contact: str = CONTACT, date: str | None = DAY,
                budget: str = BUDGET) -> dict:
    """一条送礼意图（槽位名与 `classifier.SLOT_SCHEMA['gift_plan']` 一致）。"""
    # 2026-10-24：平台 / 收货地址 / 平台订单号必填（人类要求）
    slots: dict = {"contact": contact, "budget": budget, "platform": "京东",
                   "address": "上海市某某路 1 号", "order_no": "JD-PLAN-1"}
    if date is not None:
        slots["date"] = date
    return {"intent": "gift_plan", "confidence": 0.95, "slots": slots, "missing_slots": []}


@pytest.fixture()
def wired(seeded: Path, monkeypatch: pytest.MonkeyPatch, clock):
    """把确认流程的时钟与会话态一起钉住，并返回合成库路径（每用例从干净状态开始）。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield seeded
    confirm_card.reset_state()


def turn(monkeypatch: pytest.MonkeyPatch, text: str, script: list[dict]):
    fake_llm(monkeypatch, script)
    return orchestrator.handle(text, session_id=SESSION)


# ---------------- ① 确认前不锁款（F20 验收第 1 条，最要紧的一条） ----------------

def test_confirmation_does_not_lock_funds(wired, monkeypatch) -> None:
    """出确认卡这一步只调 T19（只读）：`available` 一分不动、审计里没有 T16。"""
    before = account(wired)
    card = turn(monkeypatch, "送礼", [gift_intent()])
    assert card.executed is False and card.tier == "L2"
    assert "CONFIRM_CARD" in card.states and "【送礼计划确认卡】" in card.reply
    assert "预算" in card.reply and "礼物清单" in card.reply
    assert "已为" not in card.reply                      # 还没锁定，不许说「已为…锁定」
    assert account(wired) == before                      # ← available / balance 都没动
    assert plan_audits(wired) == 0                       # ← T16 未被调用
    current = confirm_card.current(SESSION)
    assert current is not None and current.requires_otp is True     # L2 要 OTP（`Turn` 不透出该字段）


def test_card_shows_available_and_balance_side_by_side(wired, monkeypatch) -> None:
    """验收第 2 条：卡面必须把「可用余额」与「账户余额」并列，才说得清锁定 ≠ 支出。"""
    card = turn(monkeypatch, "送礼", [gift_intent()])
    assert "可用余额" in card.reply and "账户余额" in card.reply
    assert "锁定只减可用余额" in card.reply


# ---------------- ② 确认 + OTP 后才锁款，且只扣 available ----------------

def test_execute_locks_available_but_never_touches_balance(wired, monkeypatch) -> None:
    """本文件的核心断言：执行后 `available -= 合计`，而 `balance` **一分不动**。"""
    before = account(wired)
    turn(monkeypatch, "送礼", [gift_intent()])
    asked = orchestrator.handle("确认", session_id=SESSION)
    assert asked.executed is False and "验证码" in asked.reply        # L2 → 先要 OTP
    assert account(wired) == before                                   # 确认了但还没锁
    done = orchestrator.handle(OTP, session_id=SESSION)
    assert done.executed is True and "VERIFY_NUMBERS" in done.states
    after = account(wired)
    assert after["available"] == before["available"] - TOTAL_CENTS     # 可用余额减少
    assert after["balance"] == before["balance"]                       # 账户余额不变（不是支出）
    assert plan_audits(wired) > 0
    assert confirm_card.current(SESSION) is None                       # 确认一次性消费


def test_wrong_otp_does_not_lock_funds(wired, monkeypatch) -> None:
    """失败路径：OTP 错 → 不锁款。"""
    before = account(wired)
    turn(monkeypatch, "送礼", [gift_intent()])
    orchestrator.handle("确认", session_id=SESSION)
    wrong = orchestrator.handle("000000", session_id=SESSION)
    assert wrong.executed is False
    assert account(wired) == before and plan_audits(wired) == 0


# ---------------- ③ 重复提交不重复锁款 ----------------

def test_second_confirm_does_not_lock_twice(wired, monkeypatch) -> None:
    turn(monkeypatch, "送礼", [gift_intent()])
    orchestrator.handle("确认", session_id=SESSION)
    assert orchestrator.handle(OTP, session_id=SESSION).executed is True
    after = account(wired)
    # 已无在途确认 → 「确认」被当成新的一轮去分类（脚本给闲聊），不会再锁一次。
    again = turn(monkeypatch, "确认", [{"intent": "smalltalk", "confidence": 0.9,
                                       "slots": {}, "missing_slots": []}])
    assert again.executed is False
    assert account(wired) == after


# ---------------- ④ 失败路径与缺槽 ----------------

def test_budget_below_cheapest_gift_is_rejected(wired, monkeypatch) -> None:
    """预算 100 元 < 最便宜的鲜花 199 元 → 工具层直接拒，不出确认卡、不锁款。"""
    before = account(wired)
    step = turn(monkeypatch, "送礼", [gift_intent(budget="100.00")])
    assert step.executed is False and step.tier is None
    assert "预算不足" in step.reply
    assert account(wired) == before and plan_audits(wired) == 0


def test_date_before_baseline_is_rejected(wired, monkeypatch) -> None:
    """日期早于基准日（`AS_OF` = 2026-09-12）→ 工具层拒（口径在工具层，编排层不复制）。"""
    before = account(wired)
    step = turn(monkeypatch, "送礼", [gift_intent(date="2026-09-01")])
    assert step.executed is False
    assert "不能早于今天" in step.reply
    assert account(wired) == before


def test_missing_date_asks_instead_of_guessing(wired, monkeypatch) -> None:
    """完全没给日期 → 追问，**绝不擅自取今天**。"""
    before = account(wired)
    step = turn(monkeypatch, "送礼", [gift_intent(date=None)])
    assert step.executed is False and step.tier is None
    assert "日期" in step.reply
    assert account(wired) == before and plan_audits(wired) == 0
