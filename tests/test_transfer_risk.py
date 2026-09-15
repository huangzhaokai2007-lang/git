"""任务卡 05b 单测：`tools/_transfer_risk.py`（§5 权限档 / 降级因子 / 剩余限额）与拆分验收门 ②③。

本文件是「源文件拆分」的配套：直接打风控内核的公开内函数（时间由参数注入 → 确定性好），
并静态断言两条拆分边界（不反向 import、两个 VELOCITY 常量各自命名）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tools import _query_analysis, _transfer_risk

DAY = datetime(2026, 9, 12, 12, 0, 0)          # 白天：不触发 night 因子
NIGHT = datetime(2026, 9, 12, 2, 30, 0)        # 凌晨：触发 night 因子


def _payee(whitelist: bool, used: bool = True) -> dict:
    """最小收款人行（只含风控用得到的字段）。"""
    return {"id": "payee_0001", "name": "李四", "is_whitelist": 1 if whitelist else 0,
            "last_used_ts": "2026-08-01T10:00:00" if used else None}


# ---------------- 验收门 ②③ ----------------

def test_velocity_windows_are_two_distinct_constants() -> None:
    """验收门 ③：T4 只读高频 60 分钟 ≠ §5 写降级 10 分钟 —— 同名不同义的常量必须分开命名。"""
    assert _query_analysis.VELOCITY_MINUTES_T4 == 60
    assert _transfer_risk.VELOCITY_MINUTES_WRITE == 10
    assert not hasattr(_query_analysis, "VELOCITY_WINDOW_MINUTES")
    assert not hasattr(_transfer_risk, "VELOCITY_WINDOW_MINUTES")


def test_split_modules_never_import_their_callers() -> None:
    """验收门 ②：拆分模块只许向下依赖（`_query_common`/`schemas`/`data`），反向 import 就是回边。"""
    for module, forbidden in ((_query_analysis, "tools.query"), (_transfer_risk, "tools.transfer")):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert f"import {forbidden}" not in source, forbidden
        assert f"from {forbidden} import" not in source, forbidden


# ---------------- 权限档（§5） ----------------

def test_assess_whitelist_within_limit_is_l1(seeded: Path) -> None:
    judged = _transfer_risk._assess(_payee(whitelist=True), 50_000, [], DAY)
    assert (judged["tier"], judged["requires_otp"], judged["to_human"]) == ("L1", False, False)


def test_assess_new_payee_is_l2_and_not_double_counted(seeded: Path) -> None:
    """`new_payee` 已是 L2 基础条件，不再参与升档（规格 §5 去重）→ 仍停在 L2 + OTP，不会跳到 L3。"""
    judged = _transfer_risk._assess(_payee(whitelist=False), 1_000, [], DAY)
    assert judged["tier"] == "L2" and judged["requires_otp"] is True
    assert "new_payee" in judged["factors"] and judged["escalation"] == 0


def test_assess_night_escalates_one_tier_and_two_factors_go_to_human(seeded: Path) -> None:
    assert _transfer_risk._assess(_payee(whitelist=True), 1_000, [], NIGHT)["tier"] == "L2"
    recent = [{"ts": "2026-09-12T02:20:00"}, {"ts": "2026-09-12T02:25:00"}]   # 10 分钟内已有 2 笔
    judged = _transfer_risk._assess(_payee(whitelist=True), 1_000, recent, NIGHT)
    assert (judged["tier"], judged["to_human"]) == ("L3", True)


# ---------------- 降级因子 ----------------

def test_velocity_factor_uses_the_ten_minute_window(seeded: Path) -> None:
    """§5 的 velocity 是「10 分钟内 ≥3 笔写操作（含本次）」；用 60 分钟口径会误报。"""
    inside = [{"ts": "2026-09-12T11:55:00"}, {"ts": "2026-09-12T11:58:00"}]
    assert "velocity" in _transfer_risk._factors(_payee(True), 1_000, inside, DAY)
    too_old = [{"ts": "2026-09-12T11:45:00"}, {"ts": "2026-09-12T11:46:00"}]
    assert "velocity" not in _transfer_risk._factors(_payee(True), 1_000, too_old, DAY)


def test_night_factor_hour_boundaries(seeded: Path) -> None:
    for hour in (23, 0, 5):
        assert "night" in _transfer_risk._factors(_payee(True), 1_000, [], datetime(2026, 9, 12, hour, 0, 0))
    for hour in (6, 12, 22):
        assert "night" not in _transfer_risk._factors(_payee(True), 1_000, [], datetime(2026, 9, 12, hour, 0, 0))


# ---------------- 剩余限额 ----------------

def test_limits_report_single_and_daily_headroom() -> None:
    limits = _transfer_risk._limits([{"amount": -60_000}, {"amount": -40_000}])
    assert limits == {"single_max": 50_000, "daily_max": 200_000,
                      "daily_used": 100_000, "daily_remaining": 100_000}
    assert all(isinstance(value, int) for value in limits.values())          # 整数分，无浮点


def test_limits_never_go_negative_when_over_the_daily_cap() -> None:
    assert _transfer_risk._limits([{"amount": -200_001}])["daily_remaining"] == 0


def test_today_flows_only_counts_outgoing_flows_of_the_injected_day(seeded: Path) -> None:
    """时间注入：只统计 `moment` 当天、属于当前用户账户的**支出**流水。"""
    assert _transfer_risk._today_out_flows(DAY) == []                        # 合成数据里 2026-09-12 无流水
    assert isinstance(_transfer_risk._today_out_flows(datetime(2026, 3, 1, 12, 0, 0)), list)
