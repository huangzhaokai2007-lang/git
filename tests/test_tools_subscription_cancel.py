"""任务卡 06 单测（拆分后）：T11 取消订阅 —— 正常路径 / 幂等 / 状态流转 / 确认凭证 / 越权 / 原子性。

T10 在 `tests/test_tools_subscription.py`；共享常量与 `_ref` 辅助从那里 import（避免复制漂移）。
口径来源：`docs/cards/card-06.md` 第 2~3 条 + 规格 §2 T11 + §5 权限矩阵。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data import dao
from tools import subscription as sub
from tools.schemas import ErrorCode

from tests.conftest import (
    FOREIGN_SUB, FOREIGN_USER, Clock, assert_covered, assert_no_money_floats, count, raw, write_sql,
)
from tests.test_tools_subscription import CANCELLED_ID, _ref


# ---------------- T11 cancel_subscription：正常路径 ----------------

def test_t11_cancel_happy_path(seeded: Path) -> None:
    result = sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert result.ok, result.message
    assert set(result.data) == {"sub_id", "status", "effective_date"}
    assert result.data["status"] == "cancelled"
    assert result.data["effective_date"] == "2026-09-12"          # 钉死的 clock
    assert result.facts["status_before"] == "active" and result.facts["tier"] == "L2"
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "cancelled"


def test_t11_does_not_touch_transactions(seeded: Path) -> None:
    before = count(seeded, "txn")
    sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert count(seeded, "txn") == before


def test_t11_writes_exactly_one_complete_audit_row(seeded: Path) -> None:
    """写操作必须留痕：intent/tool/档位/结果/参数齐全，且不含 OTP 明文。"""
    before = count(seeded, "audit_log")
    sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["intent"] == "subscription_cancel" and row["tool"] == "cancel_subscription"
    assert row["permission_tier"] == "L2" and row["risk_level"] == "L2" and row["result"] == "success"
    assert row["session_id"] == "session-test" and row["trace_id"]
    assert "sub_0001" in row["params_json"] and "云音乐" in row["params_json"]
    assert "active" in row["params_json"]


def test_t11_message_numbers_all_come_from_facts(seeded: Path) -> None:
    result = sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert_covered(result.message, result.facts)
    assert result.facts["amount_yuan"] == "15.00"          # 1500 分，独立复算
    assert result.facts["effective_date"] in result.message


def test_t11_no_floats_no_bool_money(seeded: Path) -> None:
    result = sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert_no_money_floats(result.data, "data")
    assert_no_money_floats(result.facts, "facts")


# ---------------- T11 幂等（reviewer 必查点） ----------------

def test_t11_same_ref_replays_the_same_result_and_executes_once(seeded: Path) -> None:
    ref = _ref(target="sub_0001")
    first = sub.cancel_subscription("sub_0001", ref)
    before = count(seeded, "audit_log")
    for _ in range(4):
        again = sub.cancel_subscription("sub_0001", ref)
        assert again.ok and again.data == first.data and again.facts == first.facts
    assert count(seeded, "audit_log") == before          # 重复调用绝不再写审计（= 没再执行）
    assert raw(seeded, "SELECT COUNT(*) AS n FROM subscription WHERE id = 'sub_0001' AND status = "
                       "'cancelled'")[0]["n"] == 1


def test_t11_new_ref_on_cancelled_subscription_is_invalid_state(seeded: Path) -> None:
    """同 ref 重放 = 幂等；新 ref 再来一次 = 非法状态（口径写进交付说明）。"""
    sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    after = count(seeded, "audit_log")
    result = sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert result.error_code == ErrorCode.INVALID_STATE
    assert count(seeded, "audit_log") == after


def test_t11_seed_cancelled_subscription_is_invalid_state(seeded: Path) -> None:
    assert sub.cancel_subscription(CANCELLED_ID, _ref(target=CANCELLED_ID)).error_code == (
        ErrorCode.INVALID_STATE)


def test_t11_paused_subscription_can_be_cancelled(seeded: Path) -> None:
    write_sql(seeded, [("UPDATE subscription SET status = 'paused' WHERE id = 'sub_0003'", ())])
    result = sub.cancel_subscription("sub_0003", _ref(target="sub_0003"))
    assert result.ok and result.facts["status_before"] == "paused"


def test_t11_failed_attempt_does_not_consume_the_ref(seeded: Path) -> None:
    """前置校验失败时不消费凭证：同一 ref 再次调用仍返回同一错误（不会变成"已执行"）。"""
    ref = _ref(target=CANCELLED_ID)
    after = count(seeded, "audit_log")
    for _ in range(3):
        assert sub.cancel_subscription(CANCELLED_ID, ref).error_code == ErrorCode.INVALID_STATE
    assert count(seeded, "audit_log") == after


# ---------------- T11 确认凭证（自包含 ref） ----------------

@pytest.mark.parametrize("bogus", ["cf_made_up", "cf_00000000000000000000000000000000", "sub_0001"])
def test_t11_unknown_ref_is_forbidden(seeded: Path, bogus: str) -> None:
    """reviewer 钉死：不存在/AI 自造串 = 伪造/越权 → FORBIDDEN（不是 TOKEN_EXPIRED），并留痕 rejected。"""
    before = count(seeded, "audit_log")
    result = sub.cancel_subscription("sub_0001", bogus)
    assert result.error_code == ErrorCode.FORBIDDEN
    assert count(seeded, "audit_log") == before + 1
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["result"] == "rejected" and row["intent"] == "subscription_cancel"
    assert row["permission_tier"] == "L2" and "sub_0001" in row["params_json"]


def test_t11_expired_ref_is_token_expired_and_not_audited(seeded: Path, clock: Clock) -> None:
    ref = _ref(target="sub_0001")
    clock.tick(seconds=sub.CONFIRM_TTL_SECONDS + 1)
    before = count(seeded, "audit_log")
    result = sub.cancel_subscription("sub_0001", ref)
    assert result.error_code == ErrorCode.TOKEN_EXPIRED
    assert count(seeded, "audit_log") == before           # 良性超时不记 rejected（reviewer 钉死）
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "active"


def test_t11_ref_is_bound_to_the_action(seeded: Path) -> None:
    wrong_action = _ref(action="manage_card", target="sub_0001")
    assert sub.cancel_subscription("sub_0001", wrong_action).error_code == ErrorCode.FORBIDDEN


def test_t11_ref_is_bound_to_the_target(seeded: Path) -> None:
    other_target = _ref(target="sub_0002")
    assert sub.cancel_subscription("sub_0001", other_target).error_code == ErrorCode.FORBIDDEN
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0002'")[0]["status"] == "active"


def test_t11_ref_is_bound_to_the_user(seeded: Path) -> None:
    """凭证非本人签发 → FORBIDDEN（不是"认字符串就算确认"）。"""
    ref = _ref(target="sub_0001")
    from tools import query
    query.set_current_user(FOREIGN_USER)
    try:
        assert sub.cancel_subscription("sub_0001", ref).error_code == ErrorCode.FORBIDDEN
    finally:
        query.set_current_user(None)


def test_t11_never_accepts_a_plain_string(seeded: Path) -> None:
    """反例守卫：任何"看起来像确认"的普通字符串都不许算确认（FORBIDDEN + 逐条留痕）。"""
    before = count(seeded, "audit_log")
    for candidate in ("yes", "确认", "confirmed", "true", "1", "sub_0001"):
        assert sub.cancel_subscription("sub_0001", candidate).error_code == ErrorCode.FORBIDDEN
    assert count(seeded, "audit_log") == before + 6        # 6 次伪造尝试各留一条 rejected
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "active"


def test_issue_confirm_ref_rejects_empty_arguments(seeded: Path) -> None:
    for action, target in (("", "sub_0001"), ("   ", "sub_0001"), (sub.CANCEL_ACTION, ""), (None, "x")):
        with pytest.raises(sub.ToolError) as caught:
            sub.issue_confirm_ref(action, target)          # type: ignore[arg-type]
        assert caught.value.code == ErrorCode.INVALID_ARGUMENT


# ---------------- T11 非法参数 / 越权 / 原子性 ----------------

@pytest.mark.parametrize(("sub_id", "ref"), [("", "cf_x"), ("sub_0001", ""), (None, "cf_x"), ("sub_0001", None)])
def test_t11_empty_arguments_are_invalid(seeded: Path, sub_id: object, ref: object) -> None:
    assert sub.cancel_subscription(sub_id, ref).error_code == ErrorCode.INVALID_ARGUMENT  # type: ignore[arg-type]


def test_t11_unknown_subscription_is_not_found(seeded: Path) -> None:
    assert sub.cancel_subscription("sub_nope", _ref(target="sub_nope")).error_code == ErrorCode.NOT_FOUND


def test_t11_foreign_subscription_is_forbidden_and_untouched(foreign: Path) -> None:
    """越权：他人订阅 → FORBIDDEN，状态零变化，但**留一条 rejected 审计**（reviewer 钉死）。"""
    before = count(foreign, "audit_log")
    result = sub.cancel_subscription(FOREIGN_SUB, _ref(target=FOREIGN_SUB))
    assert result.error_code == ErrorCode.FORBIDDEN
    assert raw(foreign, "SELECT status FROM subscription WHERE id = ?", (FOREIGN_SUB,))[0]["status"] == "active"
    assert count(foreign, "audit_log") == before + 1             # 卡 14b-5：审计由工具层自己写（恰好一笔）
    assert raw(foreign, "SELECT result FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]["result"] == "rejected"


def test_t11_audit_failure_rolls_the_whole_write_back(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """原子性：审计写不进去 → 状态翻转必须回滚（不留"改了没留痕"的半成品）。"""
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(dao, "insert_audit", boom)
    before = count(seeded, "audit_log")
    with pytest.raises(RuntimeError):
        sub.cancel_subscription("sub_0001", _ref(target="sub_0001"))
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "active"
    assert count(seeded, "audit_log") == before


def test_t11_lock_is_released_after_failure(seeded: Path) -> None:
    """失败也不许把临界区锁死（否则后续所有写操作全挂）。"""
    sub.cancel_subscription("sub_nope", _ref(target="sub_nope"))
    assert sub.cancel_subscription("sub_0001", _ref(target="sub_0001")).ok


# ---------------- 红线 / 口径守卫 ----------------

def test_module_never_calls_an_llm() -> None:
    """铁律：写工具里禁止调 LLM（静态断言本模块不引用任何模型客户端）。"""
    source = Path(sub.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)


def test_threshold_constants_are_traceable() -> None:
    """阈值常量必须注明来源：300s（仿 card-05 preview_token TTL）、3 个月（卡 06 第 1 条）、L2（规格 §2）。"""
    assert sub.CONFIRM_TTL_SECONDS == 300 and sub.ZOMBIE_WINDOW_MONTHS == 3 and sub.CANCEL_TIER == "L2"
    source = Path(sub.__file__).read_text(encoding="utf-8")
    for needle in ("来源：卡 06 口径", "来源：卡 06 第 1 条", "来源：规格 §2 T11"):
        assert needle in source


def test_cycle_labels_agree_with_the_query_layer() -> None:
    """跨模块一致性：展示文案与 query 侧同口径，漂移即红（卡 05 的同类守卫）。"""
    from tools import _query_analysis
    for cycle, label in sub.CYCLE_LABELS.items():
        assert _query_analysis.CYCLE_LABELS.get(cycle) == label


def test_confirm_refs_are_not_persisted(seeded: Path) -> None:
    """凭证是进程内状态（与 card-05 的 preview_token 同族）：库里不许出现 ref 明文。"""
    ref = _ref(target="sub_0001")
    sub.cancel_subscription("sub_0001", ref)
    dump = "\n".join(str(row) for table in ("audit_log", "subscription")
                     for row in raw(seeded, f"SELECT * FROM {table}"))
    assert ref not in dump


def test_write_path_opens_exactly_one_transaction(seeded: Path) -> None:
    """台账 R1：本模块绝不自己发 `BEGIN`/`COMMIT`，写路径只借 DAO 的 `transaction()` 开**一次**事务。

    内层 DAO 原语走 `_writing()` 的 `in_transaction` 检测 join 外层（由"审计失败整体回滚"那条用例证明）。
    与 card-05 同口径：`transaction()` 不可嵌套，编排层不得在事务内调用本工具。
    """
    source = Path(sub.__file__).read_text(encoding="utf-8")
    assert not re.search(r"""execute\(\s*['"](BEGIN|COMMIT|ROLLBACK)""", source, re.I)
    assert source.count("with transaction(conn):") == 1
    assert sub.cancel_subscription("sub_0001", _ref(target="sub_0001")).ok
    assert raw(seeded, "SELECT status FROM subscription WHERE id = 'sub_0001'")[0]["status"] == "cancelled"
    assert count(seeded, "audit_log") == 2           # seed 1 条 + 本次 1 条
