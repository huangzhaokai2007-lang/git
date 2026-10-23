"""收款人状态意图（加白/加黑/解除）走**统一解析关卡**：标签、黑名单一套口径。

v2 修复：这三个意图原先只走 T6 子串匹配 —— 「把房东加入白名单」解析失败（"房东"是标签），
「把张小美移出黑名单」在黑名单里的人身上拿不到 FORBIDDEN 之外的任何结果。
"""

from __future__ import annotations

import pytest

from agent._write_payee import _shape_blacklist, _shape_unblacklist, _shape_whitelist
from tests.conftest import raw
from tools import payee

ZHANG, WANGWU = "张小美", "王五"


@pytest.fixture()
def wired(seeded):
    yield seeded


def _pid(path, name: str) -> str:
    return raw(path, "SELECT id FROM payee WHERE name = ?", (name,))[0]["id"]


def test_whitelist_shape_resolves_by_tag(wired) -> None:
    """「把房东加入白名单」→ 标签命中王五（原先 T6 会解析失败）。"""
    got = _shape_whitelist({"payee": "房东"})
    assert got.error_code is None and got.missing == []
    assert got.filled["payee_id"] == _pid(wired, WANGWU)
    assert got.filled["masked_phone"] == "137****2003"


def test_whitelist_shape_rejects_blacklisted_person(wired) -> None:
    """加白一个已拉黑的人 → FORBIDDEN（黑白互斥，先解除再谈加白）。"""
    payee.set_payee_blacklist(_pid(wired, ZHANG), "诈骗")
    got = _shape_whitelist({"payee": ZHANG})
    assert got.error_code == "FORBIDDEN"
    assert "黑名单" in got.message


def test_blacklist_shape_resolves_blacklisted_person(wired) -> None:
    """重复拉黑：能解析出来，由 prepare 报「已经在黑名单里」（而不是转账口的 FORBIDDEN 文案）。"""
    payee.set_payee_blacklist(_pid(wired, ZHANG), "诈骗")
    got = _shape_blacklist({"payee": ZHANG})
    assert got.error_code is None and got.missing == []
    assert got.filled["payee_id"] == _pid(wired, ZHANG)


def test_unblacklist_shape_resolves_blacklisted_person(wired) -> None:
    """解除黑名单的**必要前提**：黑名单里的人也能被解析出来。"""
    payee.set_payee_blacklist(_pid(wired, ZHANG), "诈骗")
    got = _shape_unblacklist({"payee": ZHANG})
    assert got.error_code is None and got.missing == []
    assert got.filled["payee_id"] == _pid(wired, ZHANG)


def test_blacklist_shape_still_rejects_unknown(wired) -> None:
    """零命中照旧追问，不擅自选人。"""
    got = _shape_whitelist({"payee": "查无此人"})
    assert got.error_code is None and got.missing == ["payee"]
    assert "没有找到匹配的收款人" in got.clarify
