"""F12/F13 单测：理财申购/赎回端到端 —— 编排层（确认卡 + OTP 闸门 + 产品定位）。

沿用仓库做法：**真工具 + 假 LLM**（只替意图分类与润色）。工具层的校验/幂等/事务已在
`test_tools_wealth_trade.py`，**本文件只测编排层特有行为**：

① 正常路径：产品名 → 真实 id → 确认卡 → OTP → 执行（F12/F13 验收第 2 条）；
② 确认与 OTP **之前**不动余额与持仓（F13 验收第 2 条）；
③ OTP 闸门在编排层：错 OTP / 不确认 → 一律不执行；
④ 槽位追问与产品定位：缺槽 CLARIFY、未知产品 NOT_FOUND、**重名不猜**；
⑤ 工具层拒绝码原样透传（编排层不吞、不改写）；
⑥ 回执数字全部来自 facts，且未降级（铁律 2）。

**每条用例用独立 session_id**：确认卡是进程内在途状态，共用 session 会互相污染。

F12 验收清单见 `docs/features/F12-wealth-buy.md`，F13 见 `docs/features/F13-wealth-redeem.md`。
"""

from __future__ import annotations

import json
import uuid

import pytest

from agent import llm, orchestrator
from tools import wealth
from tools.schemas import ErrorCode

from tests.conftest import assert_covered

OTP = "123456"                                  # 规格 §5：demo 固定验证码
MMF = "prod_r1_mmf"                             # 稳盈货币基金：R1、活期、起购 1 元、有持仓 200 元
MIXED = "prod_r3_mixed365"                      # 混合优选 365 天：R3、365 天、起购 10000 元
#: 15 题答卷（14 道选择 + 年龄）；与 `tests/test_tools_wealth_risk.py` 同一批事实
_BASE = ("experience", "income", "income_level", "asset_ratio", "drawdown", "horizon",
         "goal", "reaction", "knowledge", "structure_exp", "liquidity", "liability",
         "concentration", "volatility")
R1 = {"age": 22, **{key: 0 for key in _BASE}}                                  # → 15 分 → R1
R3 = {"age": 35, **{key: 2 for key in _BASE}}                                  # → 44 分 → R3


@pytest.fixture()
def sess():
    """独立会话（确认卡是进程内在途状态，共用会串）。"""
    sid = f"sess-f12-{uuid.uuid4().hex[:8]}"
    yield sid
    orchestrator.confirm_card.clear(sid)


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：第一项喂意图分类，其后每次润色原样返回（数字校验天然通过）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def out(intent: str, *, slots: dict | None = None, confidence: float = 0.9) -> dict:
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


def say(text: str, session: str) -> object:
    return orchestrator.handle(text, session_id=session)


def holding_amount(product_id: str) -> int:
    conn = wealth.dao.connection()
    rows = conn.execute("SELECT amount FROM holding WHERE user_id = ? AND product_id = ?"
                        " AND status = 'held'", (wealth.current_user_id(), product_id)).fetchall()
    return sum(int(row["amount"]) for row in rows)


def balance() -> int:
    return int(wealth.dao.get_balance("savings")["balance"])


# ---------------- ① 正常路径：确认卡 → OTP → 执行 ----------------

def test_buy_runs_confirm_then_otp_then_executes(seeded, monkeypatch, sess) -> None:
    """F12 验收第 2 条：L2 确认卡 + OTP 之后才交易，三步各自的状态都说清楚。"""
    orchestrator.submit_assessment(R1, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": "稳盈货币基金", "amount": "100"})])

    first = say("买 100 块稳盈货币基金", sess)
    assert not first.executed and first.tier == "L2"
    assert "CONFIRM_CARD" in first.states and first.tool_calls == ["preview_trade"]
    assert "稳盈货币基金" in first.reply and "确认" in first.reply

    second = say("确认", sess)                       # 进 OTP 阶段，仍未执行
    assert not second.executed and "CONFIRM_CARD" in second.states

    third = say(OTP, sess)
    assert third.executed and third.error_code is None and third.tier == "L2"
    assert "稳盈货币基金" in third.reply


def test_redeem_runs_the_same_loop(seeded, monkeypatch, sess) -> None:
    """F13 正常路径：赎回同样走确认卡 + OTP，执行后持仓扣减。"""
    fake_llm(monkeypatch, [out("wealth_redeem", slots={"product": MMF, "amount": "50"})])

    first = say("赎回 50 块稳盈货币基金", sess)
    assert not first.executed and "CONFIRM_CARD" in first.states
    before = holding_amount(MMF)

    say("确认", sess)
    third = say(OTP, sess)
    assert third.executed and third.error_code is None
    assert holding_amount(MMF) == before - 5_000            # 50 元 = 5000 分


# ---------------- ② 确认与 OTP 之前不动账（F13 验收第 2 条） ----------------

def test_confirm_and_otp_do_not_touch_balance_or_holding(seeded, monkeypatch, sess) -> None:
    """F13 验收第 2 条：确认卡阶段与等 OTP 阶段，余额和持仓**一个分都没动**。"""
    orchestrator.submit_assessment(R1, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": MMF, "amount": "100"})])

    say("买 100 块稳盈货币基金", sess)
    taken = (balance(), holding_amount(MMF))
    say("确认", sess)
    assert (balance(), holding_amount(MMF)) == taken

    say("000000", sess)                                   # 错 OTP：也一个分都没动
    assert (balance(), holding_amount(MMF)) == taken


# ---------------- ③ OTP 闸门在编排层 ----------------

def test_wrong_otp_is_rejected_and_nothing_executes(seeded, monkeypatch, sess) -> None:
    """OTP 错 → 拒绝且不执行；错误次数累计，错满 3 次锁会话（卡 10 第 4 条）。"""
    orchestrator.submit_assessment(R1, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": MMF, "amount": "100"})])
    say("买 100 块稳盈货币基金", sess)
    say("确认", sess)
    taken = (balance(), holding_amount(MMF))

    wrong = say("000000", sess)
    assert not wrong.executed and "验证码不正确" in wrong.reply
    say("000000", sess)
    locked = say("000000", sess)
    assert not locked.executed and "锁定" in locked.reply
    assert (balance(), holding_amount(MMF)) == taken


def test_not_confirming_voids_the_trade(seeded, monkeypatch, sess) -> None:
    """在确认卡阶段说别的 → 本笔作废（回 SLOT_FILL），**不执行**。"""
    orchestrator.submit_assessment(R1, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": MMF, "amount": "100"})])
    say("买 100 块稳盈货币基金", sess)

    voided = say("算了，不买了", sess)
    assert not voided.executed and holding_amount(MMF) == 2_000_000    # 持仓仍是 seed 原值


# ---------------- ④ 槽位追问与产品定位 ----------------

def test_missing_slots_trigger_clarify(seeded, monkeypatch, sess) -> None:
    """只说了「买理财」→ CLARIFY 追问产品与金额，不进 PRECHECK。"""
    fake_llm(monkeypatch, [out("wealth_buy", slots={})])
    turn = say("买理财", sess)
    assert not turn.executed and "CONFIRM_CARD" not in turn.states
    assert "产品" in turn.reply and "金额" in turn.reply


def test_unknown_product_name_is_not_found(seeded, monkeypatch, sess) -> None:
    """产品名查不到 → NOT_FOUND（不猜一个相近的）。"""
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": "不存在的理财", "amount": "100"})])
    turn = say("买不存在的理财 100 块", sess)
    assert turn.error_code == ErrorCode.NOT_FOUND and not turn.executed
    assert turn.tool_calls == ["preview_trade"]


def test_ambiguous_product_name_is_not_guessed(seeded, monkeypatch, sess) -> None:
    """产品名命中多个 → NOT_FOUND（重名不猜，与订阅取消同口径）。"""
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": "理财", "amount": "100"})])
    turn = say("买理财 100 块", sess)
    assert turn.error_code == ErrorCode.NOT_FOUND and not turn.executed


# ---------------- ⑤ 工具层拒绝码原样透传 ----------------

@pytest.mark.parametrize(("slots", "code"), [
    ({"product": MIXED, "amount": "100"}, ErrorCode.INVALID_ARGUMENT),      # 低于起购额
    ({"product": MMF, "amount": "999999"}, ErrorCode.INSUFFICIENT_FUNDS),   # 余额不足
])
def test_tool_rejections_pass_through_unchanged(seeded, monkeypatch, sess, slots, code) -> None:
    """编排层不吞错误码：起购额/余额不足原样透传给用户。"""
    orchestrator.submit_assessment(R3, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots=slots)])
    turn = say("买理财", sess)
    assert turn.error_code == code and not turn.executed


def test_buy_without_assessment_is_refused_by_the_tool(seeded, monkeypatch, sess) -> None:
    """未测评 → 工具层 require_assessment 拒绝，编排层原样透传（F10 → F12 的衔接）。"""
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": MMF, "amount": "100"})])
    turn = say("买 100 块稳盈货币基金", sess)
    assert turn.error_code == ErrorCode.INVALID_STATE and not turn.executed


# ---------------- ⑥ 回执数字全部来自 facts ----------------

def test_receipt_numbers_all_come_from_facts(seeded, monkeypatch, sess) -> None:
    """回执数字全部来自工具层事实包，且未降级（铁律 2）。"""
    orchestrator.submit_assessment(R1, session_id=sess)
    fake_llm(monkeypatch, [out("wealth_buy", slots={"product": MMF, "amount": "100"})])
    say("买 100 块稳盈货币基金", sess)
    say("确认", sess)
    turn = say(OTP, sess)

    assert turn.executed and turn.degraded is False
    assert_covered(turn.reply, wealth.preview_trade(MMF, "buy", 10_000).facts)
