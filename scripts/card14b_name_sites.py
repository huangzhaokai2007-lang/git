"""卡 14b-3：给 `tools/*.py` 里所有 `require_owned(...)` 调用点补上 `tool="<所在函数名>"`。

为什么要补：`guard.tool_guard.require_owned` 只有拿到被调工具名，才会在越权时同时写
`audit_log.result='rejected'`（tool=被调工具名）与 `risk_event`。转发函数
（`tools/_query_common.require_owned`）本身不报名字，所以要在**每个调用点**把名字传下去。

用法：`uv run python scripts/card14b_name_sites.py`（幂等：已带 `tool=` 的行不动）。
"""

from __future__ import annotations

import pathlib
import re
import sys

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"

#: 单行内的 require_owned(...) 调用（实参里不含括号或换行；本仓调用点都是这种形状）
CALL = re.compile(r"require_owned\(([^()\n]*)\)")

#: 模块级函数定义（用于回溯"这一段属于哪个工具"）
DEFINE = re.compile(r"^def (\w+)\(")


def enclosing_function(lines: list[str], index: int) -> str | None:
    """往上找最近的模块级 `def`，作为这次调用的"工具名"。"""
    for line in reversed(lines[:index]):
        matched = DEFINE.match(line)
        if matched:
            return matched.group(1)
    return None


def patch_file(path: pathlib.Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(True)
    changed = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("def ") or "tool=" in line:
            continue                                   # 定义行跳过；已补过的跳过（幂等）
        matched = CALL.search(line)
        if matched is None:
            continue
        name = enclosing_function(lines, index)
        if name is None:
            continue
        closing = matched.end() - 1
        lines[index] = line[:closing] + f', tool="{name}"' + line[closing:]
        changed += 1
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def main() -> int:
    total = 0
    for path in sorted(TOOLS.glob("*.py")):
        count = patch_file(path)
        if count:
            print(f"{path.name}: +{count}")
        total += count
    print(f"共补 {total} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
