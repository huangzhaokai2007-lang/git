"""演示预设（T53 `demo_presets`）—— 界面「演示预设」面板的数据来源。

只读工具的四个断言：能给全三张演示卡（卡号 + 6 位密码）、收款人一览带类型、
限额与 OTP 齐全、**不写库**。
"""

from __future__ import annotations

from data import dao
from tools import demo_info


def test_lists_the_three_demo_cards(seeded) -> None:
    result = demo_info.demo_presets()
    assert result.ok is True
    cards = result.data["cards"]
    assert len(cards) == 3
    for card in cards:
        assert card["card_no"].isdigit() and len(card["card_no"]) == 16
        assert len(card["password"]) == 6 and card["password"].isdigit()


def test_payees_carry_their_kind(seeded) -> None:
    payees = demo_info.demo_presets().data["payees"]
    kinds = {person["name"]: person["kind"] for person in payees}
    assert kinds["王五"] == "白名单"          # 种子：白名单
    assert kinds["张小美"] == "普通"
    assert all(person["phone"].count("*") >= 4 for person in payees)   # 手机号必须脱敏


def test_limits_and_otp_are_present(seeded) -> None:
    facts = demo_info.demo_presets().data["facts"]
    assert facts["otp"] == "123456"
    assert facts["limits_single_max_yuan"] and facts["limits_daily_max_yuan"]
    assert isinstance(facts["night_from_hour"], int)


def test_it_does_not_write_anything(seeded) -> None:
    before = dao.connection().execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    demo_info.demo_presets()
    after = dao.connection().execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    assert after == before                  # 只读工具不写审计
