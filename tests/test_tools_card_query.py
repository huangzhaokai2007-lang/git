"""卡 23 单测：T18 `list_cards` + DAO 原语 + 回执模板 + 编排层路由（正常 / 边界 / 非法）。

沿用仓库既有做法：真工具 + 真模板 + **假 LLM**（monkeypatch `llm.chat_json`），库是 `seeded` 合成库，
所以断言的是真实 facts；独立口径辅助（`raw` / `assert_covered`…）取自 `tests/conftest.py`，不复用被测实现。

库里 3 张卡（卡 23 第 4 条）：`card_savings_0001`（储蓄 normal）、`card_credit_0002`
（信用 normal，额度 30,000.00 元）、`card_savings_0003`（储蓄 **lost** —— 有信息量的那条必须覆盖）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent import llm, orchestrator, templates
from data import dao
from data._dao_core import CARD_STATUSES as DAO_CARD_STATUSES
from data._dao_core import list_cards as list_card_rows
from data.seed import USER_ID
from tools import card_query
from tools._query_common import set_current_user
from tools.schemas import CARD_STATUSES, ToolResult

from tests.conftest import (
    FOREIGN_CARD, FOREIGN_USER, assert_covered, assert_no_money_floats, count, raw,
)

#: DAO 按 id 排序（卡 23 的稳定口径）
CARD_IDS = ["card_credit_0002", "card_savings_0001", "card_savings_0003"]
LOST_ID, CREDIT_ID = "card_savings_0003", "card_credit_0002"
CREDIT_LIMIT = 3_000_000                                  # 30,000.00 元（卡 23 第 4 条）
_MASK = re.compile(r"^\d{4} \*{4} \*{4} \d{4}$")
ITEM_KEYS = {"card_id", "card_no_mask", "type", "status", "credit_limit", "single_limit", "daily_limit"}
HAPPY_STATES = ["IDLE", "CLASSIFY", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]


def cards(status: str | None = None) -> ToolResult:
    """调工具并断言成功（失败即用例失败，消息里带原因）。"""
    result = card_query.list_cards(status)
    assert result.ok, result.message
    return result


def verdict(intent: str, slots: dict | None = None, confidence: float = 0.9) -> dict:
    """一份假的分类结果（形状同规格 §3 的 IntentOut）。"""
    return {"intent": intent, "confidence": confidence, "slots": slots or {},
            "missing_slots": [], "unsafe_reason": None}


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: dict) -> None:
    """假 `llm.chat_json`：分类按脚本出招，润色原样返回（数字不动）。"""

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(script)

    monkeypatch.setattr(llm, "chat_json", chat_json)


# ---------------- T18：正常 ----------------


def test_returns_all_cards_of_the_current_user(seeded: Path) -> None:
    result = cards()
    assert result.data["total_count"] == 3 == len(result.data["items"])
    assert [item["card_id"] for item in result.data["items"]] == CARD_IDS
    assert result.facts["total_count"] == 3
    assert result.error_code is None
    # 契约键名/个数冻结：`data` 只 2 个键，每项只这 7 个键（多一个键就是改契约）
    assert set(result.data) == {"items", "total_count"}
    assert all(set(item) == ITEM_KEYS for item in result.data["items"])


def test_masks_are_copied_verbatim_and_no_full_card_number_ever_appears(seeded: Path) -> None:
    """红线：库里只有 `card_no_mask` → 照抄；不得拼接/补全/猜测出完整卡号。"""
    rows = raw(seeded, "SELECT id, card_no_mask FROM card WHERE user_id = ? ORDER BY id", (USER_ID,))
    result = cards()
    assert [item["card_no_mask"] for item in result.data["items"]] == [row["card_no_mask"] for row in rows]
    for item in result.data["items"]:
        assert _MASK.match(item["card_no_mask"]), f"卡号不是库里那种脱敏形态：{item['card_no_mask']!r}"
    dumped = json.dumps({"data": result.data, "facts": result.facts}, ensure_ascii=False)
    assert not re.search(r"\d{13,}", dumped), "出现了 13 位以上连续数字（疑似完整卡号）"


def test_savings_has_null_credit_limit_while_credit_card_has_the_real_one(seeded: Path) -> None:
    """储蓄卡没有授信额度 → `None`（不是 0、不是编一个数）；信用卡给真额度（整数分）。"""
    by_id = {item["card_id"]: item for item in cards().data["items"]}
    assert by_id["card_savings_0001"]["credit_limit"] is None
    assert by_id[LOST_ID]["credit_limit"] is None
    assert by_id[CREDIT_ID]["credit_limit"] == CREDIT_LIMIT


def test_facts_carry_the_numbers_the_receipt_prints(seeded: Path) -> None:
    """铁律 2：回执里每个数字都能在 facts 找到（含卡号里的数字与额度的元形态）。"""
    result = cards()
    reply = templates.render("card_query", {**result.facts, "status_cn": ""})
    assert_covered(reply, result.facts)
    assert f"{CREDIT_LIMIT // 100:,}.00" in reply          # 30,000.00 元（复查独立算过）
    assert all(item["card_no_mask"] in reply for item in result.data["items"])


def test_data_and_facts_are_float_free(seeded: Path) -> None:
    result = cards()
    assert_no_money_floats(result.data, "data")
    assert_no_money_floats(result.facts, "facts")


# ---------------- T18：边界（过滤 / 空结果 / 冻结） ----------------


@pytest.mark.parametrize(("status", "expected"), (("lost", [LOST_ID]), ("normal", [CREDIT_ID, "card_savings_0001"]),
                                                  ("locked", [])))
def test_status_filter_returns_only_that_state(seeded: Path, status: str, expected: list[str]) -> None:
    result = cards(status)
    assert [item["card_id"] for item in result.data["items"]] == expected
    assert result.data["total_count"] == len(expected)     # 拍板 Q2：过滤后条数 = len(items)
    assert result.facts["total_count"] == len(expected)
    assert all(item["status"] == status for item in result.data["items"])


def test_frozen_is_a_legal_status_and_returns_zero(seeded: Path) -> None:
    """拍板 Q3：`frozen` 是库里合法状态 → **认它、回 0 条**（不是 INVALID_ARGUMENT）。

    「合法状态却查不了」会比空结果更怪；`frozen` 目前没有生产者（T12 无该动作），保留以对齐 schema。
    """
    result = cards("frozen")
    assert result.ok is True and result.data["total_count"] == 0 and result.data["items"] == []
    assert result.error_code is None
    assert templates.render("card_query", {**result.facts, "status_cn": "已冻结"}) == "您名下没有已冻结的卡。"


def test_empty_result_has_its_own_sentence_not_a_broken_list_header(seeded: Path) -> None:
    reply = templates.render("card_query", {**cards("locked").facts, "status_cn": "已锁定"})
    assert reply == "您名下没有已锁定的卡。"
    assert "：" not in reply and "0 张" not in reply        # 不许渲染成「共 0 张：」这种残句


def test_receipt_says_lost_in_plain_words(seeded: Path) -> None:
    """口径：面向用户说大白话 —— 已挂失的写「已挂失」，不出现内部枚举 `lost`。"""
    result = cards("lost")
    reply = templates.render("card_query", {**result.facts, "status_cn": "已挂失"})
    assert "已挂失" in reply and "lost" not in reply
    assert "6222 **** **** 0003" in reply and "0001" not in reply and "0002" not in reply
    assert reply.splitlines()[-1].startswith("- ") and "已挂失" in reply


def test_all_cards_receipt_lists_three_lines_with_status_words(seeded: Path) -> None:
    reply = templates.render("card_query", {**cards().facts, "status_cn": ""})
    lines = reply.splitlines()
    assert lines[0] == "您名下共有 3 张卡："
    assert len(lines) == 4                                  # 表头 + 3 张卡
    assert "已挂失" in lines[-1] and lines[-1].startswith("- 6222 **** **** 0003")
    # 额度只印在真有授信额度的那张（信用卡）—— 储蓄卡不许印出 0 或编一个数
    assert "额度 30,000.00 元" in lines[1]
    assert "额度" not in lines[2] and "额度" not in lines[3]


# ---------------- T18：非法入参 ----------------


@pytest.mark.parametrize("bad", ("abc", "", "LOST", "…lost", 123, True, ["lost"], {"status": "lost"}))
def test_unrecognized_status_is_invalid_argument(seeded: Path, bad: object) -> None:
    """拍板 Q3：不在四值内 → `INVALID_ARGUMENT`，**绝不静默忽略**（否则用户以为过滤过了）。"""
    result = card_query.list_cards(bad)                     # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert "status" in result.message and "abc" not in result.message


def test_schema_and_data_layer_agree_on_the_four_statuses() -> None:
    assert CARD_STATUSES == DAO_CARD_STATUSES == ("normal", "locked", "lost", "frozen")


# ---------------- 归属（他人的卡一张都不能出现） ----------------


def test_other_users_cards_never_appear(foreign: Path) -> None:
    """`foreign` 里马洛里有张卡（id 更小）→ 张三的清单里不许出现它。"""
    result = cards()
    assert [item["card_id"] for item in result.data["items"]] == CARD_IDS
    assert FOREIGN_CARD not in json.dumps(result.data, ensure_ascii=False)


def test_switching_the_session_user_switches_the_visible_cards(foreign: Path) -> None:
    """归属由 DAO 的 `user_id` 条件圈定：换成马洛里 → 只看得到她那 1 张，张三的 3 张一张都不出现。"""
    try:
        set_current_user(FOREIGN_USER)
        other = cards()
        assert [item["card_id"] for item in other.data["items"]] == [FOREIGN_CARD]
    finally:
        set_current_user(None)
    assert [item["card_id"] for item in cards().data["items"]] == CARD_IDS


def test_empty_db_returns_an_empty_list(blank: Path) -> None:
    result = cards()
    assert result.ok is True and result.data == {"items": [], "total_count": 0}
    assert result.message.strip() and "0" not in result.message


# ---------------- DAO 原语（`data/_dao_core.list_cards`） ----------------


def test_dao_primitives_sorts_filters_and_rejects_unknown_status(seeded: Path) -> None:
    assert [row["id"] for row in list_card_rows(USER_ID)] == CARD_IDS
    assert [row["id"] for row in list_card_rows(USER_ID, "lost")] == [LOST_ID]
    assert list_card_rows(USER_ID, "frozen") == []
    with pytest.raises(ValueError):
        list_card_rows(USER_ID, "abc")
    with pytest.raises(ValueError):
        list_card_rows("   ")


# ---------------- 编排层路由（卡 23 第 5 条） ----------------


@pytest.mark.parametrize(("text", "slots"), (("我几张卡", {}), ("我的卡", {}),
                                             ("帮我查一下卡", {}), ("我有没有挂失的卡", {"status": "lost"})))
def test_card_questions_reach_list_cards(seeded: Path, monkeypatch: pytest.MonkeyPatch,
                                         text: str, slots: dict) -> None:
    fake_llm(monkeypatch, verdict("card_query", slots))
    turn = orchestrator.handle(text)
    assert turn.intent == "card_query" and turn.tool_calls == ["list_cards"]
    # 只读路径按 §5 恒 L0：`Turn.tier` 只在写路径回填（占位点不变），L0 落在审计行里（见下一条用例）
    assert turn.tier is None and turn.executed is False and turn.error_code is None
    assert turn.states == HAPPY_STATES
    assert "还没接通" not in turn.reply and turn.reply.startswith("您名下")


def test_routed_lost_question_only_lists_that_state(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「我有没有挂失的卡」→ 只回该状态（截图 ② 的同一路径）。"""
    fake_llm(monkeypatch, verdict("card_query", {"status": "lost"}))
    turn = orchestrator.handle("我有没有挂失的卡")
    assert turn.reply.splitlines() == ["您名下有 1 张已挂失的卡：", "- 6222 **** **** 0003（储蓄卡 · 已挂失）"]
    assert count(seeded, "audit_log") >= 1


def test_routed_question_writes_one_l0_audit_row(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只读也要留痕（铁律 5）：档位 L0、结果 success、工具名 list_cards。"""
    before = count(seeded, "audit_log")
    fake_llm(monkeypatch, verdict("card_query"))
    orchestrator.handle("我几张卡")
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["permission_tier"] == "L0" and row["result"] == "success" and row["tool"] == "list_cards"


def test_route_table_no_longer_declares_card_query_unsupported(seeded: Path) -> None:
    """卡 23：`card_query` 已在只读路由表里（`agent/read_routes.py`，orchestrator 再导出）。"""
    from agent import read_routes
    assert orchestrator.TOOL_ROUTES is read_routes.TOOL_ROUTES
    assert orchestrator.TOOL_ROUTES["card_query"][0] == "list_cards"


def test_dao_module_still_has_no_card_read_helper(seeded: Path) -> None:
    """`data/dao.py` 顶格 300 行 → 读卡原语住 `_dao_core`，没有反向把 dao.py 撑破。"""
    assert not hasattr(dao, "list_cards")
    assert len(Path(dao.__file__).read_text(encoding="utf-8").splitlines()) <= 300
