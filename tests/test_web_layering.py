"""卡 16c 单测：交互层（`interfaces/`）的两条**机器守卫** —— 之前这两条铁律没有守卫。

背景（reviewer 审 card-16 时发现）：把 `components._yuan` 加 `1.0`，864 条用例**全绿**、没有一条变红 ——
"界面不写业务逻辑 / 不改数字"（CLAUDE.md 铁律 1+2、卡 16 禁止项）此前只靠人眼 review。本文件补两条：

① **数字只解析不重算**：`components.parse_bill` 解析出来的每个数字，都必须**逐字等于**回执文本里的那个
   数字。期望值来自本文件手写的回执串（独立于被测实现），任何"+1 / ×100 / 四舍五入"都会被逐字比对抓住。
② **分层与 SQL**：`interfaces/**` 不得 import `tools` / `data` / `guard` / `sqlite3`，不得出现 SQL 语句。

两条都自证"不是假绿"：
- ① 附**元用例** `test_a_recomputed_number_would_be_caught`：把 `_yuan` 换成"加 1"的假实现，逐字比对必须发现；
- ② 断言扫描**真的覆盖**了已知界面文件、且确实读到了允许的 `agent` import（空扫描 → 直接判红），
  并把探测函数喂给合成的违规/干净源码，证明它能抓 `import tools` 与 `SELECT ... FROM`；
- ③ **卡 17b**：IM 层的不可信文本包裹必须走 `agent.channel.wrap_untrusted` 这个 agent/ 侧通道薄函数
  （`interfaces → agent → guard` 单向），不许"借"别的模块命名空间去掏 `guard/`。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = REPO_ROOT / "interfaces"


def _load_ui_module(name: str):
    """按**文件路径**加载界面模块：`interfaces/` 没有 `__init__.py`，不往 `sys.path` 里塞路径。"""
    path = UI_DIR / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"ui_{name}", path)
    assert spec is not None and spec.loader is not None, f"加载不到界面模块：{path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


components = _load_ui_module("components")

# ---------------- ① 数字只解析不重算 ----------------

#: 手写回执：形状照抄 agent/templates 的月度账单回执（一句话 + 工具层 markdown）
RECEIPT = """2026-08 账单：支出 9,152.00 元、收入 11,046.00 元，净支出 1,894.00 元。

# 月度账单报告 · 2026-08

## 总览

- 本期支出合计：9,152.00 元（47 笔）
- 本期收入合计：11,046.00 元（2 笔）
- 净额：1,894.00 元
- 较上一账期：-32%（上期 13,456.00 元）

## 支出明细（按占比）

| 项目 | 金额（元） | 占比 |
| --- | --- | --- |
| 电商 | 4,370.00 | 48% |
| 转账 | 3,469.00 | 38% |
| 生活缴费 | 593.00 | 6% |
| 餐饮 | 570.00 | 6% |
| 交通 | 98.00 | 1% |
| 订阅 | 52.00 | 1% |
"""

#: 回执里的**字面数字**（期望值来源：本文件的字面量 —— 不复用被测实现算出来的值）
EXPECTED_PERIOD, EXPECTED_TOTAL = "2026-08", 9152.00
EXPECTED_CATEGORIES = (("电商", 4370.00, 48), ("转账", 3469.00, 38), ("生活缴费", 593.00, 6),
                       ("餐饮", 570.00, 6), ("交通", 98.00, 1), ("订阅", 52.00, 1))


def _mismatches(parsed: dict, *, period: str = EXPECTED_PERIOD, total: float = EXPECTED_TOTAL,
                categories: tuple = EXPECTED_CATEGORIES) -> list[str]:
    """逐字比对：解析结果与回执里的字面数字不符的地方（空 = 只解析、没重算）。"""
    problems: list[str] = []
    if parsed.get("period") != period:
        problems.append(f"period 期望 {period!r} 实际 {parsed.get('period')!r}")
    if parsed.get("total") != total:
        problems.append(f"total 期望 {total!r} 实际 {parsed.get('total')!r}")
    got = tuple((item["key"], item["amount"], item["pct"]) for item in parsed.get("categories", []))
    if got != categories:
        problems.append(f"categories 期望 {categories} 实际 {got}")
    return problems


def test_parse_bill_echoes_the_receipt_numbers_verbatim() -> None:
    """回执里的每一个数字都必须原样出现（分类、占比、合计、账期）。"""
    assert _mismatches(components.parse_bill(RECEIPT)) == []


def test_parse_bill_supports_the_analysis_shape_too() -> None:
    """另一种回执形状（bill_analysis 的一句话）同样只回显、不重算。"""
    parsed = components.parse_bill("2026-09 共支出 200.00 元，环比 -5%。")
    assert _mismatches(parsed, period="2026-09", total=200.00, categories=()) == []


def test_parse_bill_returns_nothing_without_a_total() -> None:
    """回执里没有可解析的支出合计 → 空结果（**不编数字**、不兜底成 0）。"""
    assert components.parse_bill("请补充时间范围。") == {}
    assert components.parse_bill("") == {}


@pytest.mark.parametrize(("raw", "value"), [("0.00", 0.00), ("1,234.56", 1234.56),
                                            ("999,900.00", 999900.00), ("9,152.00", 9152.00)])
def test_yuan_is_pure_formatting(raw: str, value: float) -> None:
    """`_yuan` 只把「元」串转成坐标点：不去重算、不缩放、不四舍五入。"""
    assert components._yuan(raw) == value


def test_total_comes_from_the_receipt_line_not_from_summing_the_categories() -> None:
    """合计**取自回执里写的那个数**，不是把分类加起来 —— 故意把两者写成不等来分辨。

    （只比对"数字改了没有"抓不到"就地重算出一个恰好相同的结果"，这条补上这个洞。）
    """
    inconsistent = ("2026-08 账单：支出 9,152.00 元\n"
                    "| 电商 | 4,370.00 | 48% |\n| 转账 | 3,000.00 | 32% |\n")
    parsed = components.parse_bill(inconsistent)
    assert parsed["total"] == 9152.00                       # 不是 7,370.00（4,370 + 3,000）
    assert [item["amount"] for item in parsed["categories"]] == [4370.00, 3000.00]


def test_a_recomputed_number_would_be_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """**元用例**：把 `_yuan` 换成"加 1"的假实现 → 上面的逐字比对必须发现（否则守卫是假绿）。

    reviewer 当初就是这么试的（`_yuan + 1.0` 全绿）；真被改数字时这条必须能亮红灯。
    """
    monkeypatch.setattr(components, "_yuan",
                        lambda text: float(str(text).replace(",", "")) + 1.0)
    assert _mismatches(components.parse_bill(RECEIPT)) != [], "改数字竟然没被发现 —— 守卫是假的"


# ---------------- ② 分层与 SQL ----------------

#: 界面层**禁止** import 的顶层包（`interfaces/` 只能调 `agent/` 与自己人）
FORBIDDEN_IMPORT_ROOTS = ("tools", "data", "guard", "sqlite3")

#: SQL 语句痕迹（写 SQL 只可能写在字符串常量里；注释与文档字符串不参与）
SQL_PATTERNS = re.compile(
    r"\b(select\b[^;]{0,60}?\bfrom\b|insert\s+into|update\s+\w+\s+set|delete\s+from|"
    r"create\s+table|drop\s+table|pragma\s+\w+)", re.IGNORECASE)


def forbidden_imports(source: str) -> list[str]:
    """AST 精确判定越层 import（`import tools` / `from data.dao import x` / `import sqlite3`）。

    用 AST 而不是 grep：`app.py` 的文档字符串里正当地提到了"sqlite3"（解释线程绑定），
    grep 会误伤；AST 只看真正的 import 语句。
    """
    tree = ast.parse(source)
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots.append((node.module or "").split(".")[0])
    return sorted({root for root in roots if root in FORBIDDEN_IMPORT_ROOTS})


def sql_hits(source: str) -> list[str]:
    """字符串常量里的 SQL 语句痕迹（写 SQL 只可能是字符串；注释/文档字符串不误伤）。"""
    tree = ast.parse(source)
    return sorted({match.group(0) for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   for match in SQL_PATTERNS.finditer(node.value)})


def ui_sources() -> dict[Path, str]:
    """`interfaces/**/*.py` 的源码（按路径排序，便于逐条报平安/报问题）。"""
    return {path: path.read_text(encoding="utf-8") for path in sorted(UI_DIR.rglob("*.py"))}


def test_the_scan_really_covers_the_interface_tree() -> None:
    """防空扫描假绿：必须真扫到已知界面文件，且确实读到了**允许**的 `agent` import。"""
    sources = ui_sources()
    names = {path.name for path in sources}
    assert {"app.py", "components.py", "redteam_page.py"} <= names, f"扫描没覆盖界面文件：{sorted(names)}"
    assert any("from agent import" in text for text in sources.values()), \
        "扫描没读到允许的 `from agent import …` —— 扫描逻辑本身失效了"


def test_interface_tree_never_imports_tools_data_guard_or_sqlite3() -> None:
    problems = [f"{path.name}: {root}" for path, text in ui_sources().items()
                for root in forbidden_imports(text)]
    assert not problems, "界面层越层 import（只能调 agent/）：\n  " + "\n  ".join(problems)


def test_interface_tree_never_writes_sql() -> None:
    problems = [f"{path.name}: {hit!r}" for path, text in ui_sources().items() for hit in sql_hits(text)]
    assert not problems, "界面层出现 SQL 语句（读数据一律走 agent/ → tools/）：\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("bad", [
    "import tools\n", "import tools.transfer\n", "from tools import query\n",
    "from data.dao import get_balance\n", "import data.db\n", "from guard import injection\n",
    "import sqlite3\n",
])
def test_detector_catches_forbidden_imports(bad: str) -> None:
    """探测器自证：真的能抓上述每种越层写法（否则 tree 用例永远绿）。"""
    assert forbidden_imports(bad), f"漏掉了：{bad!r}"


@pytest.mark.parametrize("clean", [
    "from agent import orchestrator\n", "import components as ui\n", "from pathlib import Path\n",
    "import streamlit as st\n", "from dotenv import load_dotenv\n",
])
def test_detector_allows_the_permitted_imports(clean: str) -> None:
    """不误伤合法 import（同目录组件 / agent / 标准库 / 第三方 UI 库）。"""
    assert forbidden_imports(clean) == []


@pytest.mark.parametrize("bad", [
    'conn.execute("SELECT * FROM txn")', 'cur.execute("INSERT INTO txn VALUES (1)")',
    '"UPDATE account SET balance = 0"', '"DELETE FROM txn WHERE id = ?"',
    '"CREATE TABLE scratch (id TEXT)"', '"PRAGMA table_info(txn)"',
])
def test_detector_catches_sql_in_code(bad: str) -> None:
    assert sql_hits(bad), f"漏掉了：{bad!r}"


# ---------------- ③ IM 通道的包裹入口（卡 17b） ----------------

#: IM 层的通道模块：它是 interfaces/ 里唯一调包裹的地方
IM_CHANNEL = UI_DIR / "im" / "channel.py"


def test_im_channel_wraps_through_the_agent_seam() -> None:
    """卡 17b：IM 层包裹不可信文本必须走 `agent.channel.wrap_untrusted`（interfaces→agent→guard）。

    反例（卡 17 的原写法 `orchestrator.injection.wrap_untrusted`）：能过"只查 import 语句"的分层守卫，
    但脆弱（编排层哪天不再 import `injection` 就静默失效），语义上也等于接口层直接掏护栏层。
    """
    assert IM_CHANNEL.exists(), f"没有 IM 通道模块：{IM_CHANNEL}"
    source = IM_CHANNEL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and (node.module or "") == "agent.channel"
                for alias in node.names}
    assert "wrap_untrusted" in imported, \
        "interfaces/im/channel.py 必须 import agent.channel.wrap_untrusted（不许各层自己去掏 guard/）"
    borrows = [node.value.id for node in ast.walk(tree)                # 形如 `<某模块>.injection`
               if isinstance(node, ast.Attribute) and node.attr == "injection"
               and isinstance(node.value, ast.Name)]
    assert not borrows, f"不许借别的模块的命名空间掏护栏层（发现 {borrows}；用 agent.channel.wrap_untrusted）"


def test_detector_does_not_flag_chinese_prose_or_docstrings() -> None:
    """不误伤：文档字符串里正当提到 sqlite3/线程绑定、以及中文业务词（"更新""删除"）。"""
    assert sql_hits('"""说明：sqlite3 的连接有线程亲和，本层不写 SQL。"""') == []
    assert sql_hits('st.caption("已取消这笔操作：未提交执行")') == []
