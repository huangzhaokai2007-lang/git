"""查看白名单（T20 `list_payees`）单测 —— 分组 / 脱敏 / 归属 / 空列表。

需求来源：用户要求「新增一个查看白名单的功能，可以看到白名单与非白名单人员的手机号」。
它不在 F15–F22 六项之内，是 `SPEC-CHANGE` 新增的只读能力（规格 §2 T20 + §3 `payee_list`）。

覆盖点（逐条对应铁律与验收）：
- **表格排版**：精简 Markdown 表格，**一行一位收款人**（同名两位各占一行，不合并）；
- **分组正确**：白名单用「白名单」列（✅ / —）标出，**行序照抄 facts**（白名单优先），
  表下一行说明两组的转账验证方式差别（这是用户看这张表的用途）；
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

from agent import llm, orchestrator, templates
from agent.render_lists import (T_PAYEE_EMPTY, T_PAYEE_NO, T_PAYEE_TABLE_HEAD, T_PAYEE_TABLE_SEP,
                                T_PAYEE_YES, render_payees)
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


# ---------------- ① 表格排版 + 分组 + 脱敏（走完整链路：意图 → T20 → 渲染） ----------------

def _table_rows(text: str) -> list[list[str]]:
    """把回执里的 Markdown 表格解析成行（**跳过表头与分隔行**），用来校「一行一位、顺序照抄」。"""
    lines = [line for line in text.splitlines() if line.startswith("|")]
    assert lines[0] == T_PAYEE_TABLE_HEAD, lines[0]
    assert lines[1] == T_PAYEE_TABLE_SEP, lines[1]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]


def test_whitelist_table_has_one_row_per_payee_in_facts_order(seeded: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    turn = ask(monkeypatch)
    assert turn.executed is False                                    # 只读，没有执行任何写
    assert turn.tool_calls == ["list_payees"]
    # 档位看**审计行**（`permission_tier`）：只读路径不写 `Turn.tier`（`ctx.tier` 只在写路径与
    # payee/risk 分支被赋值），跑 yaml 的 runner 也是查审计行 —— 这里与它同口径。
    row = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert row["intent"] == "payee_list" and row["permission_tier"] == "L0"
    rows = _table_rows(turn.reply)
    assert len(rows) == 5                                            # 干净库 5 位 → 一行一位
    assert [item[0] for item in rows].count("李四") == 2              # 同名两位各占一行，不合并
    # 分组正确：靠「白名单」列表达（不再是【分组】小标题）
    marks = {item[0]: item[3] for item in rows if item[0] in (WHITE_NAME, OTHER_NAME)}
    assert marks[WHITE_NAME] == T_PAYEE_YES                          # 王五 是白名单
    assert marks[OTHER_NAME] == T_PAYEE_NO                           # 张小美 不是
    # 行序照抄 facts（工具层已按「白名单优先 + 姓名」排好）：编排层不重排
    flags = [item[3] == T_PAYEE_YES for item in rows]
    assert flags == sorted(flags, reverse=True), rows
    # 脱敏形式照抄「手机号」列
    phones = [item[1] for item in rows]
    assert WHITE_PHONE in phones and OTHER_PHONE in phones


def test_structural_table_reply_skips_polish(seeded: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """表格是**结构**不是措辞：`payee_list` 不进润色（否则润色器会把表格压平成一串文字）。

    两半都测：① 本意图 `polish` **0 次**；② 别的意图（`balance_query`）**照旧润色** ——
    后半条是防「把润色全局关掉」的守卫，只测前半条的话那种回归查不出来。
    """
    calls: list[str] = []
    real_polish = templates.polish

    def counting_polish(text: str, facts: dict) -> str | None:
        calls.append(text)
        return real_polish(text, facts)

    monkeypatch.setattr(templates, "polish", counting_polish)
    turn = ask(monkeypatch)
    assert _table_rows(turn.reply)                                    # 表格原样交付，没被重排
    assert calls == [], calls                                         # 表格回执一次润色都没走

    fake_llm(monkeypatch, [{"intent": "balance_query", "confidence": 0.95,
                            "slots": {"account_type": "savings"}, "missing_slots": []}])
    orchestrator.handle("查一下余额", session_id=SESSION)
    assert calls, "其它意图应照旧走润色（守卫：润色不能被全局关掉）"


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
