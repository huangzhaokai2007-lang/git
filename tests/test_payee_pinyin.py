"""收款人解析的**多音字消歧** + **直接输拼音**（`agent/payee_resolve`）。

| 验收点 | 用例 |
|---|---|
| 多音字：单（shàn）/ 山 能同音命中 | `test_polyphonic_characters_hit` |
| 逐字读音集合比：不同读音不命中 | `test_different_readings_miss` |
| 直接打拼音：`zhangxiaomei` → 张小美 | `test_ascii_pinyin_query_resolves` |
| 拼写展开有上限（不猜、不爆炸） | `test_py_forms_are_capped` |
"""

from __future__ import annotations

import pytest

from agent import payee_resolve as pr
from tests.conftest import raw

SESSION = "sess-pinyin"


def test_polyphonic_characters_hit() -> None:
    """「单」有 {dan, chan, shan} 三种读法 → 用户说「山明」也能命中「单明」。"""
    assert pr._py_hit("山明", "单明") is True
    assert pr._py_hit("单明", "单明") is True
    assert pr._py_hit("曾老师", "增老师") is True          # 曾 {ceng, zeng}
    assert pr._py_hit("解伟", "谢伟") is True              # 解 {jie, xie}


def test_different_readings_miss() -> None:
    """读音不重叠就不命中（不能把同音判成万金油）。"""
    assert pr._py_hit("王五", "赵六") is False
    assert pr._py_hit("王五", "赵六六") is False           # 字数不同
    assert pr._py_hit("", "王五") is False


def test_ascii_pinyin_query_resolves(seeded) -> None:
    """用户直接打拼音 `zhangxiaomei` → 解析到「张小美」（走 pinyin 层，不是子串）。"""
    result = pr.resolve("zhangxiaomei")
    assert result.kind == pr.KIND_UNIQUE and len(result.payees) == 1
    assert result.payees[0]["name"] == "张小美" and result.how == pr.HOW_PINYIN


def test_py_forms_are_capped() -> None:
    """多音字展开有上限（8 种），不会指数爆炸。"""
    assert len(pr._py_forms("单明")) <= 8
    assert "shanming" in pr._py_forms("单明")
    assert pr._py_forms("") == set()
