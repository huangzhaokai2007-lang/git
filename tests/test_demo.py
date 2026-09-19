"""卡 19b 单测：演示脚本（`scripts/demo.py`）的自断言函数 —— 演示不能只是"看起来跑了"。

`scripts/demo.py` 是现场演示与离线兜底的主力（12/12 自断言），但它的断言函数此前没有任何单测。
这里覆盖：记分器、临时合成库、以及三段进程内演示（IM / 注入 / 写路径）在**干净临时库**上必须全绿 ——
一旦演示话术或断言失效，这条测试会先红，而不是等到台上才发现。

（网页端那一段用 Streamlit `AppTest`，开销较大，仍由 `scripts/demo.py` 本体与卡 16 的界面自检覆盖。）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:                   # 演示脚本按"同目录脚本"方式导入
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                   # noqa: E402

from tests.conftest import balance, raw                       # noqa: E402


def _fresh_demo(monkeypatch: pytest.MonkeyPatch) -> demo.Demo:
    """造一份临时合成库并把进程指向它（与 `scripts/demo.py` 的入口同一路径）。"""
    path = demo.build_temp_db()
    monkeypatch.setenv("DB_PATH", str(path))
    return demo.Demo(path=path)


def test_check_records_and_reports(capsys: pytest.CaptureFixture) -> None:
    """记分器：通过/失败都记账并打印（失败要带细节，否则台上看不出哪儿错了）。"""
    ctx = demo.Demo(path=Path("（未使用）"))
    assert ctx.check("甲", True) is True
    assert ctx.check("乙", False, "细节") is False
    out = capsys.readouterr().out
    assert "[OK  ] 甲" in out and "[FAIL] 乙 —— 细节" in out
    assert ctx.results == [True, False]


def test_build_temp_db_seeds_an_independent_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """临时库真的建了合成数据（演示不改动仓库里的演示库）。"""
    ctx = _fresh_demo(monkeypatch)
    assert ctx.path.exists() and ctx.path.name == "bank.db"
    assert balance(ctx.path) > 0                                   # 独立复算（conftest 的直查口径）
    assert raw(ctx.path, "SELECT COUNT(*) AS n FROM txn")[0]["n"] > 0
    assert str(ctx.path.parent.name).startswith("demo19-")         # 落在临时目录里


@pytest.mark.parametrize("section", [demo.section_im, demo.section_guard, demo.section_write])
def test_inprocess_sections_all_pass(section, monkeypatch: pytest.MonkeyPatch) -> None:
    """三段进程内演示在干净库上必须全绿（断言真的会拦故障，不是走过场）。"""
    ctx = _fresh_demo(monkeypatch)
    section(ctx)
    assert ctx.results and all(ctx.results), f"{section.__name__} 有失败项"


def test_section_write_really_executes_the_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    """写路径那段确实把账写进去了：余额少 100 元（演示不是"只打印"）。"""
    ctx = _fresh_demo(monkeypatch)
    before = balance(ctx.path)
    demo.section_write(ctx)
    assert all(ctx.results)
    assert balance(ctx.path) == before - 10_000                    # 100.00 元 = 10000 分（整数分）


def test_section_cli_spawns_the_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 段走真子进程（只留一条话术，免得测试太慢）。"""
    monkeypatch.setattr(demo, "CLI_PHRASES", ("查一下余额",))
    ctx = _fresh_demo(monkeypatch)
    demo.section_cli(ctx)
    assert ctx.results == [True]


def test_main_quick_skips_the_web_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--quick` 只跳网页端那一段，其余照跑（离线兜底演示就靠它）。"""
    monkeypatch.setattr(sys, "argv", ["demo.py", "--quick"])
    assert demo.main() == 0                                        # 全程离线的自断言演示
    assert os.environ.get("DB_PATH", "").endswith("bank.db")       # 指向临时库而非仓库演示库
