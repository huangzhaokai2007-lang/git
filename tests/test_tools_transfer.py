"""任务卡 05 单测（拆分后）：接口冻结 + T6 收款人解析 + T7 转账预览（`tools/transfer.py`）。

时间由 conftest 的 `clock` fixture 钉死（12:00 白天），避免凌晨因子让档位断言随运行时刻抖动。
T8 执行与 T9/红线不变量分别在 test_tools_transfer_execute.py、test_tools_transfer_aa.py（卡 04b 拆分）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from data.seed import SAVINGS_ID
from tools import _transfer_risk, query, transfer

from tests.conftest import (FOREIGN_PAYEE, PAYEE, PAYEE_NEW, OTP, Clock, write_sql, count, balance, assert_covered, preview_ok, add_today_flows)


def test_error_code_enum_has_all_ten_registered_values() -> None:
    assert sorted(code.value for code in transfer.ErrorCode) == [
        "AMBIGUOUS", "FORBIDDEN", "HALLUCINATION_BLOCKED", "INSUFFICIENT_FUNDS", "INVALID_ARGUMENT",
        "INVALID_STATE", "NOT_FOUND", "OVER_LIMIT", "TOKEN_EXPIRED", "TOO_MANY_ROWS"]


def test_data_keys_are_frozen_to_the_spec(seeded: Path) -> None:
    assert set(transfer.resolve_payee("李四").data) == {"candidates", "ambiguous"}
    assert set(preview_ok().data) == {"preview_token", "fee", "tier", "requires_otp", "limits"}
    executed = transfer.execute_transfer(preview_ok().data["preview_token"])
    assert set(executed.data) == {"txn_id", "amount", "payee_name", "balance_after"}
    assert set(transfer.create_aa_request([PAYEE], 1_000).data) == {"request_id", "per_person_amount"}


def test_module_never_calls_an_llm() -> None:
    """铁律：execute 里禁止调 LLM —— 静态断言本模块不引用任何模型客户端。"""
    source = Path(transfer.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)


def test_resolve_payee_reports_ambiguity_for_same_name(seeded: Path) -> None:
    result = transfer.resolve_payee("李四")
    assert result.ok and result.data["ambiguous"] is True
    assert len(result.data["candidates"]) == 2
    assert {item["name"] for item in result.data["candidates"]} == {"李四"}
    assert len({item["masked_phone"] for item in result.data["candidates"]}) == 2
    assert result.facts["candidate_count"] == 2 and result.facts["ambiguous"] is True
    assert_covered(result.message, result.facts)


def test_resolve_payee_single_match_includes_phone_mask(seeded: Path) -> None:
    result = transfer.resolve_payee("赵六")
    assert result.ok and result.data["ambiguous"] is False
    assert result.data["candidates"] == [{"id": "payee_0005", "name": "赵六", "masked_phone": "135****4005"}]
    assert_covered(result.message, result.facts)


def test_resolve_payee_matches_phone_fragment(seeded: Path) -> None:
    result = transfer.resolve_payee("139")
    assert {item["id"] for item in result.data["candidates"]} == {"payee_0001", "payee_0002"}


def test_resolve_payee_no_match_is_empty_not_an_error(seeded: Path) -> None:
    result = transfer.resolve_payee("查无此人")
    assert result.ok and result.data == {"candidates": [], "ambiguous": False}
    assert result.facts["candidate_count"] == 0 and not re.search(r"\d", result.message)


def test_resolve_payee_hides_other_users_payees(foreign_payee: Path) -> None:
    result = transfer.resolve_payee("李四")
    assert {item["id"] for item in result.data["candidates"]} == {"payee_0001", "payee_0002"}
    assert FOREIGN_PAYEE not in {item["id"] for item in result.data["candidates"]}


@pytest.mark.parametrize("bad", ["", "   ", None, 139, ["李四"]])
def test_resolve_payee_rejects_illegal_query(seeded: Path, bad: object) -> None:
    result = transfer.resolve_payee(bad)                        # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


def test_preview_whitelist_within_limit_is_l1_without_otp(seeded: Path) -> None:
    result = preview_ok(PAYEE, 10_000)
    assert result.data["tier"] == "L1" and result.data["requires_otp"] is False
    assert result.data["fee"] == 0 and result.facts["factors"] == [] and result.facts["escalation"] == 0
    assert result.facts["ttl_seconds"] == 300
    assert result.data["preview_token"].startswith("pt_")
    assert result.data["preview_token"] not in json.dumps(result.facts)      # token 不进事实包
    assert_covered(result.message, result.facts)


def test_preview_non_whitelist_is_l2_and_new_payee_is_not_double_counted(seeded: Path) -> None:
    """规格 §5 的 L2 基础条件就是「新收款人」：它不能再把档位升到 L3（否则 L2+OTP 永远走不到）。"""
    result = preview_ok(PAYEE_NEW, 5_000)
    assert result.data["tier"] == "L2" and result.data["requires_otp"] is True
    assert result.facts["factors"] == ["new_payee"] and result.facts["escalation"] == 0
    assert result.facts["to_human"] is False


def test_preview_night_factor_escalates_one_tier(seeded: Path, clock: Clock) -> None:
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)
    result = preview_ok(PAYEE, 10_000)
    assert result.data["tier"] == "L2" and result.facts["factors"] == ["night"]
    clock.moment = datetime(2026, 9, 12, 23, 0, 0)              # 边界：23:00 命中
    assert preview_ok(PAYEE, 10_000).facts["factors"] == ["night"]
    clock.moment = datetime(2026, 9, 12, 6, 0, 0)               # 边界：06:00 不命中
    assert preview_ok(PAYEE, 10_000).facts["factors"] == []


def test_preview_velocity_needs_three_writes_within_ten_minutes(seeded: Path) -> None:
    """短时高频：含本次在内 10 分钟内 ≥3 笔写操作才命中（前两笔不命中）。"""
    assert preview_ok(PAYEE, 1_000).facts["factors"] == []
    transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"])
    assert preview_ok(PAYEE, 1_000).facts["factors"] == []       # 已有 1 笔 + 本次 = 2
    transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"])
    third = preview_ok(PAYEE, 1_000)                             # 已有 2 笔 + 本次 = 3 → 命中
    assert third.facts["factors"] == ["velocity"] and third.data["tier"] == "L2"


def test_preview_amount_jump_factor_uses_five_times_history(seeded: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """金额偏离（写操作降级因子）= 规格 §5 的 **5 倍**，与卡 04 T4 的只读检测 3 倍不是一回事。

    正常账户里 5 倍历史均值(~1,000元) 已被"单笔上限 500元"遮蔽，故这里把历史均值压小来钉住因子逻辑。
    """
    assert transfer.AMOUNT_JUMP_RATIO == 5 and transfer.HISTORY_DAYS == 90
    # 卡 05b：风控内核搬进 `_transfer_risk` 且改为**时间注入**（`moment` 由 transfer 传入，避免回边）。
    # 该函数在 `_factors` 里是**模块内**调用，故打桩目标必须是风控模块自己（patch transfer 不再命中）。
    monkeypatch.setattr(_transfer_risk, "_history_mean_cents", lambda moment: (100, 50))
    assert preview_ok(PAYEE, 501).facts["factors"] == ["amount_jump"]
    assert preview_ok(PAYEE, 500).facts["factors"] == []         # 恰好 5 倍不算（严格大于）


def test_preview_night_plus_velocity_goes_to_human(seeded: Path, clock: Clock) -> None:
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)               # 凌晨：先要过 OTP 才能落成写操作
    for _ in range(2):
        transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"], otp=OTP)
    result = preview_ok(PAYEE, 1_000)
    assert result.data["tier"] == "L3" and result.facts["to_human"] is True
    assert set(result.facts["factors"]) == {"night", "velocity"}


def test_preview_writes_absolutely_nothing(seeded: Path) -> None:
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    preview_ok(PAYEE, 10_000)
    preview_ok(PAYEE_NEW, 5_000)
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == before


def test_preview_reports_remaining_limits_from_today(seeded: Path) -> None:
    result = preview_ok(PAYEE, 10_000)
    assert result.data["limits"] == {"single_max": 50_000, "daily_max": 200_000,
                                     "daily_used": 0, "daily_remaining": 200_000}
    assert result.facts["limits_daily_remaining_yuan"] == "2,000.00"
    add_today_flows(seeded, [("2026-09-12T09:00:00", -160_000)])
    used = preview_ok(PAYEE, 30_000)
    assert used.facts["limits_daily_used"] == 160_000 and used.data["limits"]["daily_remaining"] == 40_000


def test_preview_enforces_single_and_daily_hard_limits(seeded: Path) -> None:
    over_single = transfer.preview_transfer(PAYEE, 50_001)
    assert over_single.error_code == "OVER_LIMIT" and over_single.data is None
    assert transfer.preview_transfer(PAYEE, 50_000).ok                       # 恰好上限：放行
    add_today_flows(seeded, [("2026-09-12T09:00:00", -160_000)])
    assert transfer.preview_transfer(PAYEE, 50_000).error_code == "OVER_LIMIT"      # 160000+50000 > 200000
    assert preview_ok(PAYEE, 40_000).facts["limits_daily_used"] == 160_000         # 恰好不超：放行


def test_preview_reports_insufficient_funds(seeded: Path) -> None:
    write_sql(seeded, [("UPDATE account SET balance = 100, available = 100 WHERE id = ?", (SAVINGS_ID,))])
    result = transfer.preview_transfer(PAYEE, 5_000)
    assert result.ok is False and result.error_code == "INSUFFICIENT_FUNDS"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)
    assert transfer.preview_transfer(PAYEE, 100).ok                          # 恰好够：放行


def test_preview_rejects_unknown_or_foreign_payee(seeded: Path, foreign_payee: Path) -> None:
    assert transfer.preview_transfer("payee_nope", 100).error_code == "NOT_FOUND"
    refused = transfer.preview_transfer(FOREIGN_PAYEE, 100)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}


def test_preview_refuses_unimplemented_schedule_and_split(seeded: Path) -> None:
    assert transfer.preview_transfer(PAYEE, 100, schedule="2026-10-01").error_code == "INVALID_ARGUMENT"
    assert transfer.preview_transfer(PAYEE, 100, split_with=[PAYEE_NEW]).error_code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("args,kwargs", [
    (("", 100), {}), (("payee_0001", 0), {}), (("payee_0001", -100), {}),
    (("payee_0001", 1.5), {}), (("payee_0001", True), {}), (("payee_0001", "100"), {}),
    (("payee_0001", 100), {"schedule": 20261001}),
])
def test_preview_rejects_illegal_arguments(seeded: Path, args: tuple, kwargs: dict) -> None:
    result = transfer.preview_transfer(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)
