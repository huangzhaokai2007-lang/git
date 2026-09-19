"""卡 16c 变异自检：交互层守卫必须**真能被触发**（改数字 / 加越层 import / 写 SQL 都要变红）。

用法：`uv run python scripts/mutcheck_16c.py`
每个变异只做**一处文本替换**（改的是 `interfaces/` 下的源码，跑完立即逐字节还原并复核哈希 ——
源码不留任何改动），跑完看 `tests/test_web_layering.py` 是否变红。

判据同时看 FAILED 行数与 rc（卡 07 的教训：collection error 也有 rc≠0，不能只看 FAILED 行数）。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPONENTS = REPO / "interfaces/web/components.py"
APP = REPO / "interfaces/web/app.py"
TESTS = ["tests/test_web_layering.py"]

#: (文件, [(标签, 原文, 新文), ...]) —— 每次只替换一处
GROUPS: list[tuple[pathlib.Path, list[tuple[str, str, str]]]] = [
    (COMPONENTS, [
        ("① 界面偷偷改数字（`_yuan` 加 1）",
         '    return float(str(text).replace(",", ""))',
         '    return float(str(text).replace(",", "")) + 1.0'),
        ("① 界面把千分位抹掉（`_yuan` 不再去逗号）",
         '    return float(str(text).replace(",", ""))',
         "    return float(str(text))"),
        ("① 界面把分类金额就地加起来当合计",
         '    return parsed if parsed["total"] is not None else {}',
         '    parsed["total"] = sum(item["amount"] for item in parsed["categories"]) or parsed["total"]\n'
         '    return parsed if parsed["total"] is not None else {}'),
        ("② 界面越层 import 数据层",
         'import streamlit as st',
         'import streamlit as st\nfrom data.dao import get_balance'),
    ]),
    (APP, [
        ("② 界面里写 SQL",
         'PAGES = ("💬 聊天", "📊 账单图表", "🧾 审计时间轴")',
         'SQL = "SELECT * FROM txn"\nPAGES = ("💬 聊天", "📊 账单图表", "🧾 审计时间轴")'),
    ]),
]


def run_tests() -> tuple[int, int]:
    """返回 (FAILED 行数, 退出码)。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "--no-header", "-q", "-p", "no:cacheprovider"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    return len([line for line in stdout.splitlines() if line.startswith("FAILED ")]), proc.returncode


def _with_file_newlines(text: str, original: bytes) -> str:
    """锚点里的 `\\n` 换成文件实际换行（Windows 上是 CRLF，否则多行锚点匹配不上）。"""
    return text.replace("\n", "\r\n" if b"\r\n" in original else "\n")


def main() -> int:
    total = sum(len(mutations) for _, mutations in GROUPS)
    caught = 0
    for path, mutations in GROUPS:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        group_caught = 0
        failed, rc = run_tests()
        print(f"[{path.name}] 基线：failed={failed} rc={rc}（守卫全绿才算基线成立）")
        if failed or rc:
            print("基线不是全绿 → 变异自检不成立，先修基线。")
            return 1
        for label, old, new in mutations:
            original = path.read_bytes()
            text = original.decode("utf-8")
            old, new = _with_file_newlines(old, original), _with_file_newlines(new, original)
            if text.count(old) != 1:
                print(f"[锚点缺失 ✗] {label}（命中 {text.count(old)} 次）")
                return 1
            try:
                path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
                failed, rc = run_tests()
            finally:
                path.write_bytes(original)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                print(f"[还原失败 ✗] {label}")
                return 1
            red = failed > 0 or rc != 0
            caught += red
            group_caught += red
            print(f"[{'变红 ✓' if red else '仍绿 ✗ 假绿'}] {label}: failed={failed} rc={rc}")
        print(f"{path.name}：{group_caught}/{len(mutations)} 个变异如期变红")
    print(f"\n{caught}/{total} 个变异如期变红（逐字节还原、哈希复核通过，源码零改动）")
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
