"""查看白名单（T20 `list_payees`）单测 —— 分组 / 脱敏 / 归属 / 空列表。

需求来源：用户要求「新增一个查看白名单的功能，可以看到白名单与非白名单人员的手机号」。
它不在 F15–F22 六项之内，是 `SPEC-CHANGE` 新增的只读能力（规格 §2 T20 + §3 `payee_list`）。

覆盖点（逐条对应铁律与验收）：
- **分组正确**：白名单 / 非白名单分开，两组的转账验证方式不同（这是用户看这张表的用途）；
- **脱敏**：手机号一律 `139****1001` 形式，**完整号一个字都不出现**（铁律 8）；
- **归属**：只读当前 user，**他人的收款人一条都不出现**；
- **数字来自事实包**：回执里每个数字都能在 `facts` 里找到（铁律 2）；
- **空列表**：有自己的句子，不套列表头（避免「共 0 位：」这种残句）。

种子事实（`data/seed.py:47-52`，**干净库**）：白名单 2 位（李四 139****1001 / 王五 137****2003），
非白名单 3 位（李四 139****1002 / 张小美 136****3004 / 赵六 135****4005）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import llm, orchestrator
from agent.render_lists import T_PAYEE_EMPTY, render_payees
from guard import facts_check

from tests.conftest import raw

SESSION = "sess-payee-list"
WHITE_NAME, WHITE_PHONE = "王五", "137****2003"          # is_whitelist = 1
OTHER_NAME, OTHER_PHONE = "张小美", "136****3004"         # is_whitelist = 0
FOREIGN_PHONE = "139****9999"                            # `conftest.foreign_payee` 名下（他人）


def fake_llm(monkeypatch: pytest.MonkeyPatch, script: list[dict]) -> None:
    """假 LLM：分类按脚本出结果；润色把原文原样返回（数字一个不动）。"""
    queue = list(script)

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=json.loads(user)["reply"])
        return schema.model_validate(queue.pop(0))

    monkeypatch.setattr(llm, "chat_json", chat_json)


def ask(monkeypatch: pytest.MonkeyPatch, text: str = "我的白名单有哪些"):
    fake_llm(monkeypatch, [{"intent": "payee_list", "confidence": 0.95,
                            "slots": {}, "missing_slots": []}])
    return orchestrator.handle(text, session_id=SESSION)


# ---------------- ① 分组 + 脱敏（走完整链路：意图 → T20 → 渲染） ----------------

def test_whitelist_and_other_are_grouped_with_masked_phones(seeded: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    turn = ask(monkeypatch)
    assert turn.executed is False                                    # 只读，没有执行任何写
    assert turn.tool_calls == ["list_payees"]
    # 档位看**审计行**（`permission_tier`）：只读路径不写 `Turn.tier`（`ctx.tier` 只在写路径与
    # payee/risk 分支被赋值），跑 yaml 的 runner 也是查审计行 —— 这里与它同口径。
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["intent"] == "payee_list" and row["permission_tier"] == "L0"
    assert "【白名单】" in turn.reply and "【非白名单】" in turn.reply
    assert WHITE_NAME in turn.reply and OTHER_NAME in turn.reply
    assert WHITE_PHONE in turn.reply and OTHER_PHONE in turn.reply   # 脱敏形式照抄
    # 分组正确：白名单那两位必须出现在「【白名单】」段里
    white_section = turn.reply.split("【非白名单】")[0]
    assert WHITE_NAME in white_section and OTHER_NAME not in white_section


def test_no_full_phone_number_ever_appears(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """铁律 8：完整 11 位手机号一个字都不许出现（库里本来就只存脱敏串）。"""
    turn = ask(monkeypatch)
    stored = [row["phone"] for row in raw(seeded, "SELECT phone FROM payee")]
    assert all("*" in phone for phone in stored)                     # 入库即脱敏
    assert not [phone for phone in stored if phone in turn.reply and "*" not in phone]


def test_counts_come_from_the_facts_package(seeded: Path) -> None:
    """铁律 2：回执里每个数字都能在事实包里找到（本用例只喂渲染层，直接校事实包）。"""
    facts = {"total_count": 5, "whitelist_count": 2, "non_whitelist_count": 3,
             "items": [{"payee_id": "p1", "name": WHITE_NAME, "masked_phone": WHITE_PHONE,
                        "bank": "建设银行", "is_whitelist": True},
                       {"payee_id": "p2", "name": OTHER_NAME, "masked_phone": OTHER_PHONE,
                        "bank": "中国银行", "is_whitelist": False}]}
    text = render_payees(facts)
    assert facts_check.verify_numbers(text, facts)[0] is True


# ---------------- ② 归属：他人的收款人一条都不出现 ----------------

def test_other_users_payee_never_appears(seeded: Path, foreign_payee: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    turn = ask(monkeypatch)
    assert FOREIGN_PHONE not in turn.reply
    assert raw(seeded, "SELECT COUNT(*) AS n FROM payee")[0]["n"] > 0   # 库里确实有他人收款人


# ---------------- ③ 空列表：自己的句子，不套列表头 ----------------

def test_empty_payee_list_has_its_own_sentence() -> None:
    """零收款人分支：**自己的句子，不套列表头**（避免「共 0 位：」这种残句）。

    `render_payees` 是纯函数（只吃 facts），故这里直接喂零事实包，不需要数据库夹具。
    """
    text = render_payees({"total_count": 0, "whitelist_count": 0, "non_whitelist_count": 0,
                          "items": []})
    assert text == T_PAYEE_EMPTY
    assert "0" not in text                                           # 不报「0 位」（那是残句）
