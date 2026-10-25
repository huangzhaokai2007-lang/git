"""AA 按人金额的两个实测坑（回归守卫）。

1. **同名待选时不要校验按人金额**：用户说「向李四，妈妈，房东，aa 收款各 30,40,50」，
   李四同名进解析卡；若此时就拿「李四 30」跟已解析名单比 → 报「「李四」不在这次 AA 的名单里」，
   把用户挡在门外。
2. **按人金额要认用户原话**：名单里的真名是「张小美、王五」，而用户说的是「妈妈、房东」，
   只按真名比对同样会误报。
"""

from __future__ import annotations

import pytest

from agent import _write_aa


def test_per_person_accepts_real_names() -> None:
    picked = {"payee_0001": "李四", "payee_0003": "王五"}
    cents, err = _write_aa._parse_per_person("李四 30、王五 50", picked)
    assert err is None and cents == [3_000, 5_000]


def test_per_person_accepts_user_words_as_aliases() -> None:
    """「妈妈 / 房东」是标签，不是真名 —— 但必须认。"""
    picked = {"payee_0004": "张小美", "payee_0003": "王五"}
    aliases = {"妈妈": "payee_0004", "房东": "payee_0003"}
    cents, err = _write_aa._parse_per_person("妈妈 40、房东 50", picked, aliases)
    assert err is None and cents == [4_000, 5_000]


def test_per_person_rejects_strangers() -> None:
    picked = {"payee_0001": "李四"}
    cents, err = _write_aa._parse_per_person("赵六 30", picked)
    assert cents is None and "不在这次 AA 的名单里" in str(err)


def test_per_person_reports_the_missing_person() -> None:
    picked = {"payee_0001": "李四", "payee_0003": "王五"}
    cents, err = _write_aa._parse_per_person("李四 30", picked)
    assert cents is None and "还缺" in str(err) and "王五" in str(err)


def test_same_name_pending_does_not_block_per_person_amounts(seeded) -> None:
    """名单里有同名待选（李四）时，**先出解析卡**，而不是报「不在名单里」。"""
    shaped = _write_aa._shape_aa({
        "payee_ids": ["李四", "妈妈", "房东"],
        "amounts": "李四 30、妈妈 40、房东 50",
    })
    assert shaped.error_code is None, f"不该报错：{shaped.message}"
    assert shaped.candidates, "同名应当带出候选（界面出解析卡）"
    assert "payee_ids" in shaped.missing
    assert shaped.filled.get("per_person_cents") is None, "名单没解析全之前不要定按人金额"
