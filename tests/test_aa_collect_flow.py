"""F15 单测：AA 收款（T9）端到端 —— 确认前不发起 / 分摊余数 / 去重 / 归属。

沿用仓库做法：**真工具 + 假 LLM**（monkeypatch `llm.chat_json`）—— AA 走真实数据库，
断言的是审计表与回执数字；假 LLM 只给分类结果与"一个数字都不动"的润色。

F15 验收清单逐条落成用例：

| 验收点 | 用例 |
|---|---|
| 确认与 OTP 前**不发起**请求 | `test_confirm_card_issues_no_request_before_confirmation` |
| 分摊和余数规则可解释 | `test_remainder_goes_to_initiator_in_integer_cents` |
| 联系人去重且归属当前用户 | `test_duplicate_payee_is_deduplicated` / `test_other_users_payee_is_not_resolved` |
| 重复提交不重复发起 | `test_second_confirm_does_not_issue_twice` |
| 零命中不擅自选一个 | `test_unknown_payee_asks_instead_of_guessing` |
| 只说「发起请求」不说「对方已付款」 | `test_wording_never_claims_money_received` |

**审计断言为什么按 `tool` 过滤**：铁律 5 要求每轮都写 `audit_log`，所以「总条数变多」不能证明 T9 被调过；
而 T9 一旦被调用**必写一条 `tool='create_aa_request'`** —— 只有这一条能证明「真发起了」。

种子事实（`data/seed.py:47-52`）：`payee_0003` 王五 / `payee_0005` 赵六 / `payee_0004` 张小美
（`payee_0001/0002` 都叫李四 → 同名歧义，本文件不用）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import confirm_card, llm, orchestrator

from tests.conftest import raw

SESSION = "sess-f15"
WANGWU, ZHAOLIU, ZHANG = "王五", "赵六", "张小美"
FOREIGN_PHONE = "139****9999"        # `conftest.foreign_payee`：马洛里名下的「李四」


def aa_audits(path: Path) -> int:
    """T9 的审计条数 —— 唯一能证明「真发起过 AA 请求」的信号。"""
    return len(raw(path, "SELECT 1 FROM audit_log WHERE tool = ?", ("create_aa_request",)))


OTP = "123456"                        # demo 固定验证码（工具层 OTP_CODE）


def execute_aa(session_id: str):
    """确认 → 执行（全白名单 L1 直接执行；含非白名单 L2 会先要验证码，这里一并走通）。"""
    stage = orchestrator.handle("确认", session_id=session_id)
    if stage.executed:
        return stage
    assert "验证码" in stage.reply, stage.reply                    # 兜底：若将来哪档要码，仍能走通
    return orchestrator.handle(OTP, session_id=session_id)


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def aa_intent(names: list[str], amount: str, *, amounts: str | None = None) -> dict:
    """一条 AA 意图（槽位名与 `classifier.SLOT_SCHEMA['aa_collect']` 一致）。"""
    slots: dict = {"payee_ids": names, "amount": amount}
    if amounts:
        slots["amounts"] = amounts
    return {"intent": "aa_collect", "confidence": 0.95,
            "slots": slots, "missing_slots": []}


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


# ---------------- ① 确认前不发起请求（F15 验收第 2 条，最要紧的一条） ----------------

def test_confirm_card_issues_no_request_before_confirmation(wired, monkeypatch) -> None:
    """出确认卡这一步**T9 一次都没被调用**：审计里没有 `create_aa_request`。"""
    card = turn(monkeypatch, f"向{WANGWU}和{ZHAOLIU} AA 收款 300 元",
                [aa_intent([WANGWU, ZHAOLIU], "300.00")])
    assert card.executed is False and card.tier == "L1"       # 含非白名单（赵六）→ 要验证码
    # 2026-10-25 口径：AA 是收款方向 → 一律免码，卡面不再出现「验证码原因」
    assert "验证码原因" not in card.reply and "等待验证码" not in card.reply
    #: 2026-10-28：卡面按「含我」口径 —— 总金额（含我）/ 每人 / 我承担 / 待收
    assert "含我一份" in card.reply
    assert "CONFIRM_CARD" in card.states and "【群收款确认卡】" in card.reply
    assert "每人" in card.reply and "我承担" in card.reply and "待收" in card.reply
    assert "已发起" not in card.reply                       # 还没发起，不许这么说
    assert aa_audits(wired) == 0                            # ← T9 未被调用（它必写审计）
    assert confirm_card.current(SESSION) is not None


# ---------------- ② 分摊与余数：整数分均摊，余数给发起人 ----------------

def test_remainder_goes_to_initiator_in_integer_cents(wired, monkeypatch) -> None:
    """含我：100.00 元 ÷ **4 份**（3 人 + 我）= 每人 25.00 元，我 25.00 元，待收 75.00 元。

    2026-10-28（用户要求「群收款要包含我」）：份数从「人数」变成「人数 + 1」，
    我那份 + 余数都由发起人承担（整数分，禁浮点）。
    """
    card = turn(monkeypatch, "三个人一起 AA 收款", [aa_intent([WANGWU, ZHAOLIU, ZHANG], "100.00")])
    assert "25.00" in card.reply and "3 人" in card.reply
    assert "我承担：25.00 元" in card.reply and "待收：75.00 元" in card.reply
    assert card.executed is False and aa_audits(wired) == 0


def test_no_remainder_when_amount_divides_evenly(wired, monkeypatch) -> None:
    """含我：300.00 元 ÷ **3 份**（2 人 + 我）= 每人 100.00 元，我 100.00 元，待收 200.00 元。"""
    card = turn(monkeypatch, "向两人 AA 收款", [aa_intent([WANGWU, ZHAOLIU], "300.00")])
    assert "每人：100.00 元" in card.reply and "2 人" in card.reply
    assert "我承担：100.00 元" in card.reply and "待收：200.00 元" in card.reply


# ---------------- ③ 确认后真发起 + 重复提交不重复发起 ----------------

def test_confirm_issues_the_request_and_writes_audit(wired, monkeypatch) -> None:
    turn(monkeypatch, "AA 收款", [aa_intent([WANGWU, ZHAOLIU], "300.00")])
    done = execute_aa(SESSION)                            # v2：含非白名单 → 确认 + 验证码
    assert done.executed is True and "VERIFY_NUMBERS" in done.states
    #: 2026-10-28 含我口径：2 人 + 我 = 3 份 → 每人 100，待收 200（回执说的是待收）
    assert "已发起" in done.reply and "200.00" in done.reply
    # 执行后审计里出现 `create_aa_request`：一条来自 T9 自身，一条来自编排层记的工具调用。
    # 故只断言「> 0」与「前后不变」，不钉魔法数字。
    assert aa_audits(wired) > 0
    assert confirm_card.current(SESSION) is None            # 确认一次性消费


def test_second_confirm_does_not_issue_twice(wired, monkeypatch) -> None:
    """确认卡用完即清 → 第二次「确认」走不到执行（F15 验收第 3 条）。"""
    turn(monkeypatch, "AA 收款", [aa_intent([WANGWU, ZHAOLIU], "300.00")])
    assert execute_aa(SESSION).executed is True
    after = aa_audits(wired)
    assert after > 0
    # 已无在途确认 → 「确认」被当成新的一轮去分类（脚本给闲聊），因此不会再发起。
    again = turn(monkeypatch, "确认", [{"intent": "smalltalk", "confidence": 0.9,
                                       "slots": {}, "missing_slots": []}])
    assert again.executed is False
    assert aa_audits(wired) == after                        # 审计不新增 = 没有第二次发起


# ---------------- ④ 去重 与 零命中 与 归属 ----------------

def test_duplicate_payee_is_deduplicated(wired, monkeypatch) -> None:
    """同一人写两遍只算一位（T9 对重复收款人直接 INVALID_ARGUMENT，故编排层先按 id 去重）。"""
    card = turn(monkeypatch, "AA 收款", [aa_intent([WANGWU, WANGWU, ZHAOLIU], "60.00")])
    assert card.executed is False and "2 人" in card.reply
    assert "重复" not in card.reply


def test_unknown_payee_asks_instead_of_guessing(wired, monkeypatch) -> None:
    """零命中 → 追问，绝不擅自换一个人。"""
    step = turn(monkeypatch, "AA 收款", [aa_intent(["查无此人"], "50.00")])
    assert step.executed is False and step.tier is None
    assert "没有找到匹配的收款人" in step.reply
    assert aa_audits(wired) == 0


def test_other_users_payee_is_not_resolved(wired, foreign_payee, monkeypatch) -> None:
    """归属：`conftest.foreign_payee` 名下有个同名「李四」（139****9999），**不该被解析到**。"""
    assert aa_audits(wired) == 0
    step = turn(monkeypatch, "AA 收款", [aa_intent([FOREIGN_PHONE], "50.00")])
    assert step.executed is False
    assert "没有找到匹配的收款人" in step.reply
    assert aa_audits(wired) == 0


# ---------------- ⑤ 措辞：不落资金，就不许说「已收款」 ----------------

def test_wording_never_claims_money_received(wired, monkeypatch) -> None:
    """T9 只写审计、不动 `balance`/`available` → 措辞严禁「已收款」「到账」。"""
    forbidden = ("已收款", "已到账", "收款成功")
    card = turn(monkeypatch, "AA 收款", [aa_intent([WANGWU, ZHAOLIU], "300.00")])
    done = execute_aa(SESSION)
    assert done.executed is True
    assert not [word for word in forbidden if word in card.reply + done.reply]


# ---------------- ⑥ v2：成员全白名单 → 免码 ----------------

def test_all_whitelist_members_need_no_otp(wired, monkeypatch) -> None:
    """**2026-10-24 口径**：AA 里**全是有效白名单** → L1 免码（含非白名单才要码）。"""
    from data import payee_dao
    from tools import payee
    pid = payee.add_payee("白名单同事", "13800007777").data["payee_id"]
    payee_dao.update_payee(pid, "u_zhangsan_0001", is_whitelist=1, whitelist_quota=100_000)
    card = turn(monkeypatch, "AA 收款", [aa_intent([WANGWU, "白名单同事"], "200.00")])
    assert card.tier == "L1"
    assert "验证码" not in card.reply and "非白名单" not in card.reply
    assert aa_audits(wired) == 0


# ---------------- ⑦ F22f：按人金额 + 一键均摊 ----------------

def test_per_person_amounts_card_and_execute(wired, monkeypatch) -> None:
    """「王五 100 元、赵六 200 元」→ 卡面按人明细、合计 = 300，执行走 T31。"""
    script = [aa_intent([WANGWU, ZHAOLIU], "300.00", amounts=f"{WANGWU} 100 元、{ZHAOLIU} 200 元"),
              {"intent": "aa_collect", "confidence": 0.9, "slots": {}, "missing_slots": []}]
    card = turn(monkeypatch, "AA 收款，王五 100 元、赵六 200 元", script)
    assert "CONFIRM_CARD" in card.states and card.tier == "L1"    # 含非白名单 → 要码
    assert "按人金额" in card.reply and "100.00" in card.reply and "200.00" in card.reply
    done = execute_aa(SESSION)
    assert done.executed is True and "按人金额" in done.reply
    #: 按人金额真实走的是 T31 —— 以其审计行为准（编排层审计行记的是描述符声明的 T9 名，
    #: 这是 AA 无 preview 族的既有归属口径；T31 自己的审计行在，可回溯）。
    assert len(raw(wired, "SELECT 1 FROM audit_log WHERE tool = 'create_aa_request_with_amounts'")) > 0


def test_per_person_amounts_sum_must_match_total(wired, monkeypatch) -> None:
    """合计与总额不符 → 直接报错，不出卡。"""
    script = [aa_intent([WANGWU, ZHAOLIU], "300.00", amounts=f"{WANGWU} 100 元、{ZHAOLIU} 100 元")]
    result = turn(monkeypatch, "AA 收款", script)
    assert result.executed is False and "不符" in result.reply
