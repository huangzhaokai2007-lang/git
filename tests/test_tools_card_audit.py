"""任务卡 06 单测（拆分后）：T12 幂等 / 越权 / 原子性 / 审计留痕 / 红线守卫。

状态机、双因子、L2-L3 事实包、限额校验在 `tests/test_tools_card_guards.py`；
冻结面与正常路径在 `tests/test_tools_card.py`（共享辅助从那里 import）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from data import dao
from tools import card, subscription as sub
from tools import transfer
from tools.schemas import ErrorCode

from tests.conftest import FOREIGN_CARD, OTP, count, raw
from tests.test_tools_card import SAVINGS, _call, _ref, _row


# ---------------- 幂等 / 越权 / 原子性 ----------------

def test_same_ref_replays_the_same_result_and_executes_once(seeded: Path) -> None:
    ref = _ref(SAVINGS)
    first = _call(SAVINGS, "lock", ref=ref)
    before = count(seeded, "audit_log")
    for _ in range(4):
        again = _call(SAVINGS, "lock", ref=ref)
        assert again.ok and again.data == first.data and again.facts == first.facts
    assert count(seeded, "audit_log") == before              # 重复调用不再写审计（没再执行）


def test_replayed_ref_still_validates_otp(seeded: Path) -> None:
    """幂等重放也必须过双因子：拿不到 OTP 的人不能靠"重放"读回结果。"""
    ref = _ref(SAVINGS)
    assert _call(SAVINGS, "lock", ref=ref).ok
    assert _call(SAVINGS, "lock", ref=ref, otp="000000").error_code == ErrorCode.FORBIDDEN


def test_new_ref_on_a_locked_card_is_invalid_state(seeded: Path) -> None:
    assert _call(SAVINGS, "lock").ok
    assert _call(SAVINGS, "lock").error_code == ErrorCode.INVALID_STATE


def test_foreign_card_is_forbidden_and_untouched(foreign: Path) -> None:
    """越权：他人卡 → FORBIDDEN，状态零变化，但留一条 rejected 审计（reviewer 钉死）。"""
    before = count(foreign, "audit_log")
    result = _call(FOREIGN_CARD, "lock")
    assert result.error_code == ErrorCode.FORBIDDEN
    assert raw(foreign, "SELECT status FROM card WHERE id = ?", (FOREIGN_CARD,))[0]["status"] == "normal"
    assert count(foreign, "audit_log") >= before + 1             # 卡 14b-3：越权双写（工具自身 + tool_guard 各留一笔）
    row = raw(foreign, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["result"] == "rejected" and FOREIGN_CARD in row["params_json"]


def test_unknown_card_is_not_found(seeded: Path) -> None:
    assert _call("card_nope", "lock").error_code == ErrorCode.NOT_FOUND


def test_audit_failure_rolls_the_write_back(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(dao, "insert_audit", boom)
    before = count(seeded, "audit_log")
    with pytest.raises(RuntimeError):
        _call(SAVINGS, "lock")
    assert _row(seeded, SAVINGS)["status"] == "normal"
    assert count(seeded, "audit_log") == before


def test_lock_is_released_after_every_failure_mode(seeded: Path) -> None:
    _call(SAVINGS, "lock", ref="cf_made_up")
    _call(SAVINGS, "lock", otp=None)
    _call(SAVINGS, "lock", cvv="1")
    assert _call(SAVINGS, "lock").ok                         # 临界区没被锁死


def test_otp_is_never_written_to_the_database(seeded: Path) -> None:
    _call(SAVINGS, "lock")
    dump = "\n".join(str(row) for row in raw(seeded, "SELECT * FROM audit_log"))
    assert card.OTP_CODE not in dump


# ---------------- 红线 / 口径守卫 ----------------

def test_module_never_calls_an_llm() -> None:
    source = Path(card.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)


def test_write_path_opens_exactly_one_transaction() -> None:
    """台账 R1：不自己发 BEGIN/COMMIT，写路径只开一次 `transaction()`。"""
    source = Path(card.__file__).read_text(encoding="utf-8")
    assert not re.search(r"""execute\(\s*['"](BEGIN|COMMIT|ROLLBACK)""", source, re.I)
    assert source.count("with transaction(conn):") == 1


def test_threshold_constants_are_traceable() -> None:
    """60s（规格 §5 L3）、OTP 固定码（规格 §5 L2）都写死了来源注释。"""
    assert card.L3_DELAY_SECONDS == 60 and card.OTP_CODE == "123456"
    source = Path(card.__file__).read_text(encoding="utf-8")
    for needle in ("来源：规格 §5 L3", "来源：规格 §5 L2", "来源：规格 §2 T12"):
        assert needle in source


def test_otp_constant_agrees_with_the_transfer_layer() -> None:
    """跨模块一致性：同一枚 demo 验证码，漂移即红。"""
    assert card.OTP_CODE == transfer.OTP_CODE == OTP


def test_confirm_ref_helper_lives_in_the_subscription_layer_for_now() -> None:
    """本轮 `confirm_ref` 机制落在 subscription 层、card 单向 import（06b 可抽 tools/_confirm.py）。"""
    assert card.check_confirm_ref.__module__ == "tools.subscription"
    assert card.finish_confirm_ref.__module__ == "tools.subscription"
    assert card.check_confirm_ref is sub.check_confirm_ref


def test_no_session_or_ref_leak_into_card_rows(seeded: Path) -> None:
    ref = _ref(SAVINGS)
    _call(SAVINGS, "lock", ref=ref)
    assert ref not in str(_row(seeded, SAVINGS))
