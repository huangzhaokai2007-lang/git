#!/usr/bin/env bash
# 一键验收：单测 → 评测用例 → 冒烟对话 → 红线检查。任一失败退出码非 0。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

FAIL=0
step() { printf '\n== %s ==\n' "$1"; }
skip() { printf '   SKIP: %s\n' "$1"; }

step "1/5 依赖与环境"
if [ -f pyproject.toml ]; then
  if command -v uv >/dev/null 2>&1; then
    if [ ! -d .venv ]; then uv sync --quiet || { echo "   uv sync 失败"; FAIL=1; }; fi
    PY="uv run python"; PT="uv run pytest"
  else
    echo "   缺少 uv：https://docs.astral.sh/uv/"; FAIL=1; PY=python; PT=pytest
  fi
else
  skip "没有 pyproject.toml（请先做卡 00）"; PY=python; PT=pytest
fi

step "2/5 单元测试"
if ls tests/test_*.py >/dev/null 2>&1; then
  $PT || FAIL=1
else
  skip "还没有测试文件（请先做卡 01）"
fi

step "3/5 评测用例 tests/cases/*.yaml"
if ls tests/cases/*.yaml >/dev/null 2>&1; then
  $PT tests/test_cases.py -s || FAIL=1        # -s：把「用例通过率」打印出来（规格 §8 要求）
else
  skip "还没有用例集（请做卡 11）"
fi

step "4/5 冒烟对话"
if [ -f app/cli.py ]; then
  $PY -m app.cli "帮我看看上个月花了多少" || FAIL=1
else
  skip "app/cli.py 未实现（请做卡 09）"
fi

step "5/5 红线检查（注入检测 + 数字校验器）"
if [ -f guard/injection.py ] && [ -f guard/facts_check.py ]; then
  $PT tests/test_injection.py tests/test_facts_check.py || FAIL=1
else
  skip "护栏未实现（请做卡 12 / 13）"
fi

if [ "$FAIL" -eq 0 ]; then
  printf '\n全部通过 ✅\n'
else
  printf '\n有失败项 ❌ —— 先修，不要提交、不要让 AI 说"完成"\n'
fi
exit "$FAIL"
