"""收款人解析关卡（`agent/payee_resolve.py`）的回归守卫 —— 转账域 v2「串联」的地基。

**为什么它值一整份测试**：转账 / 定时转账 / AA / 送礼 四个出口从此**只调这一个函数**。
它错了，四个功能会一起错；它对了，「同一个人」在四个功能里的结论才一致。

| 验收点 | 用例 |
|---|---|
| **标签指代**（「给我妈转 200」） | `test_tag_resolves_to_a_unique_payee` |
| 标签多命中 → 出解析卡，不猜 | `test_shared_tag_is_ambiguous` |
| **同音**（王晓红 ≈ 王筱虹）→ 唯一 | `test_homophone_resolves_via_pinyin` |
| **黑名单**直接拒，不当候选 | `test_blacklisted_payee_is_rejected_not_offered` |
| 零命中 → 提示 + 给新增出口 | `test_unknown_name_offers_to_add` |
| **会话记忆**：刚选过的人下一句直接命中 | `test_pick_is_remembered_within_the_session` |
"""

from __future__ import annotations

import pytest

from agent import payee_resolve
from data import payee_dao
from tests.conftest import raw
from tools import payee

UID = "u_zhangsan_0001"


@pytest.fixture()
def wired(seeded):
    payee_resolve.reset()
    yield seeded
    payee_resolve.reset()


def _add(path, name: str, phone: str, *, tags: str = "", blacklist: bool = False) -> str:
    """加一个收款人（走 T17）再补标签/黑名单（走 DAO）。返回 payee_id。"""
    result = payee.add_payee(name, phone)
    assert result.ok, result.message
    pid = result.data["payee_id"]
    fields: dict = {}
    if tags:
        fields["tags"] = tags
    if blacklist:
        fields.update(blacklist=1, blacklist_reason="测试：诈骗嫌疑")
    if fields:
        payee_dao.update_payee(pid, UID, **fields)
    return pid


def test_tag_resolves_to_a_unique_payee(wired) -> None:
    """「妈妈」→ 种子里的张小美（tags=妈妈,家人）。这是 v2 最核心的一句能力。"""
    got = payee_resolve.resolve("妈妈")
    assert got.kind == payee_resolve.KIND_UNIQUE
    assert got.how == payee_resolve.HOW_TAG
    assert got.payees[0]["name"] == "张小美"


def test_shared_tag_is_ambiguous(wired) -> None:
    """两个人都有「朋友」标签 → 必须出解析卡（绝不擅自选一个）。"""
    _add(wired, "孙七", "13800002222", tags="朋友")
    got = payee_resolve.resolve("朋友")            # 赵六（种子）+ 孙七
    assert got.kind == payee_resolve.KIND_AMBIGUOUS
    assert len(got.payees) == 2
    assert "请确认" in got.clarify


def test_homophone_resolves_via_pinyin(wired) -> None:
    """同音：说「王晓红」，库里是「王筱虹」→ 拼音全等 → 唯一命中。"""
    _add(wired, "王筱虹", "13800003333")
    got = payee_resolve.resolve("王晓红")
    assert got.kind == payee_resolve.KIND_UNIQUE
    assert got.how == payee_resolve.HOW_PINYIN
    assert got.payees[0]["name"] == "王筱虹"


def test_blacklisted_payee_is_rejected_not_offered(wired) -> None:
    """黑名单命中：结论是 `blacklisted`（拒），而**不是**把它当候选让用户选。"""
    _add(wired, "骗子甲", "13800004444", blacklist=True)
    got = payee_resolve.resolve("骗子甲")
    assert got.kind == payee_resolve.KIND_BLACKLISTED
    assert got.blocked is not None and got.blocked["name"] == "骗子甲"
    assert "黑名单" in got.clarify and "诈骗嫌疑" in got.clarify


def test_unknown_name_offers_to_add(wired) -> None:
    got = payee_resolve.resolve("查无此人")
    assert got.kind == payee_resolve.KIND_NONE
    assert "我要加个收款人" in got.clarify


def test_pick_is_remembered_within_the_session(wired) -> None:
    """会话记忆：这一句确定了「房东」，下一句同样的称呼直接命中，不再查库碰运气。"""
    first = payee_resolve.resolve("房东", session_id="s-a")
    assert first.kind == payee_resolve.KIND_UNIQUE
    payee_resolve.remember("s-a", str(first.payees[0]["payee_id"]))

    again = payee_resolve.resolve("房东", session_id="s-a")
    assert again.kind == payee_resolve.KIND_UNIQUE
    assert again.how == payee_resolve.HOW_MEMORY

    other = payee_resolve.resolve("房东", session_id="s-b")     # 别的会话不受影响
    assert other.how != payee_resolve.HOW_MEMORY
    assert len(raw(wired, "SELECT 1 FROM payee")) == 5          # 本用例没加人，仍是种子那 5 位


def test_substring_still_works_for_phone_fragment(wired) -> None:
    """回落 T6 不能丢：只报手机号后四位也要能定位（原有能力不许退化）。"""
    got = payee_resolve.resolve("1002")
    assert got.kind == payee_resolve.KIND_UNIQUE
    assert got.payees[0]["masked_phone"] == "139****1002"
