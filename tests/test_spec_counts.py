"""规格计数守卫（card-24 第 3 条接口，人类已批）：`docs/01-接口规格.md` §2 的工具表必须自洽。

**为什么把它机器化**：这坑**已栽两次** —— 卡 18 报过「标题 15 vs 表 T1–T16」、卡 20 只改标题未补行。
光靠人盯必再犯，故把「标题数字 == 表内行数 == 序号从 1 连续到 N」钉成断言。

失败必须给**可读诊断**（如「标题写 18，表内只有 17 行，缺 T17」），不是只报 False —— 这是本卡对守卫的要求。
"""

from __future__ import annotations

import re
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parents[1] / "docs" / "01-接口规格.md"

#: §2 标题行形如：`## 2. 工具函数契约（18 个，名字与字段名冻结）`
_TITLE_COUNT = re.compile(r"^##\s*2\.[^\n]*?（\s*(\d+)\s*个", re.MULTILINE)

#: §2 表内行形如：`| T7 | ... |` —— 只认**表格首列**，避免正文里的 "T7" 字样误伤
_ROW = re.compile(r"^\|\s*T(\d+)\s*\|", re.MULTILINE)


def _spec_text() -> str:
    assert SPEC_PATH.is_file(), f"找不到冻结规格：{SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


def _section_two(text: str) -> str:
    """截出 §2 正文（到下一个二级标题为止），避免把 §3 的意图编号算进来。"""
    start = text.find("## 2.")
    assert start >= 0, "规格里找不到 §2（工具函数契约）"
    nxt = text.find("\n## ", start + 1)
    return text[start:] if nxt < 0 else text[start:nxt]


def _declared_and_rows() -> tuple[int, list[int]]:
    body = _section_two(_spec_text())
    title = _TITLE_COUNT.search(body)
    assert title is not None, "§2 标题里找不到「（N 个…）」—— 规格格式可能被改动，请人工确认"
    return int(title.group(1)), [int(n) for n in _ROW.findall(body)]


def test_spec_section2_title_count_matches_table_rows() -> None:
    """标题里的工具数 == 表内 `| Tn |` 行数。"""
    declared, rows = _declared_and_rows()
    assert declared == len(rows), (
        f"§2 标题写 {declared} 个工具，表内只有 {len(rows)} 行"
        f"（表内序号：{rows}）—— 补表行或改标题，两者必须一致")


def test_spec_section2_row_numbers_are_contiguous() -> None:
    """表内序号必须从 1 连续到 N，不跳号、不重号。"""
    declared, rows = _declared_and_rows()
    assert rows, "§2 表内一行 `| Tn |` 都没找到"
    expected = list(range(1, len(rows) + 1))
    if rows == expected:
        return
    missing = sorted(set(expected) - set(rows))
    duplicated = sorted({n for n in rows if rows.count(n) > 1})
    detail = []
    if missing:
        detail.append("缺 " + "、".join(f"T{n}" for n in missing))
    if duplicated:
        detail.append("重复 " + "、".join(f"T{n}" for n in duplicated))
    assert False, (f"§2 序号不连续：标题写 {declared} 个，实际序号 {rows}"
                   f"（{'；'.join(detail) or '顺序错乱'}）")
