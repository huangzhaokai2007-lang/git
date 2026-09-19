#!/usr/bin/env bash
# 一键验收：单测 → 评测用例 → 冒烟对话 → 红线检查 → 通道/红队/评测入口自检。任一失败退出码非 0。
# 全程**不依赖外网**：单测用假 LLM；红队与两处自检都用桩（stub），不需要 LLM key。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

FAIL=0
step() { printf '\n== %s ==\n' "$1"; }
skip() { printf '   SKIP: %s\n' "$1"; }

step "1/6 依赖与环境"
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
# 本地演示库：第 6 段的通道/评测入口自检要读合成数据（单测用自己的临时库，所以这里单独兜一下）
if [ -f data/seed.py ] && [ ! -f "${DB_PATH:-data/bank.db}" ]; then
  printf '   造合成数据（本地库不存在）：'
  $PY -m data.seed --reset >/dev/null 2>&1 && echo "OK" || { echo "失败"; FAIL=1; }
fi

step "2/6 单元测试"
if ls tests/test_*.py >/dev/null 2>&1; then
  $PT || FAIL=1
else
  skip "还没有测试文件（请先做卡 01）"
fi

step "3/6 评测用例 tests/cases/*.yaml"
if ls tests/cases/*.yaml >/dev/null 2>&1; then
  $PT tests/test_cases.py -s || FAIL=1        # -s：把「用例通过率」打印出来（规格 §8 要求）
else
  skip "还没有用例集（请做卡 11）"
fi

step "4/6 冒烟对话"
if [ -f app/cli.py ]; then
  $PY -m app.cli "帮我看看上个月花了多少" || FAIL=1
else
  skip "app/cli.py 未实现（请做卡 09）"
fi

step "5/6 红线检查（注入检测 + 数字校验器）"
REDLINE_TESTS=""
[ -f guard/injection.py ] && REDLINE_TESTS="$REDLINE_TESTS tests/test_injection.py"
[ -f guard/facts_check.py ] && REDLINE_TESTS="$REDLINE_TESTS tests/test_facts_check.py"
if [ -n "$REDLINE_TESTS" ]; then
  $PT $REDLINE_TESTS || FAIL=1
else
  skip "护栏未实现（请做卡 12 / 13）"
fi

step "6/6 通道 / 红队 / 评测入口（全部离线、不占端口）"
if [ -f scripts/redteam.py ]; then
  $PY scripts/redteam.py || FAIL=1               # 30 条攻击集：未得逞/无危害，rc=1 即失败
else
  skip "还没有红队脚本（请做卡 15）"
fi
if [ -d interfaces/im ]; then
  $PY -m interfaces.im --selftest || FAIL=1      # IM 通道：包裹 + 复用编排层 + 回环出消息
else
  skip "还没有 IM 通道（请做卡 17）"
fi
if [ -f scripts/api_smoke.py ]; then
  $PY scripts/api_smoke.py || FAIL=1             # 评测入口 POST /api/chat 的契约与事实包
else
  skip "还没有评测入口冒烟（请做卡 18）"
fi

if [ "$FAIL" -eq 0 ]; then
  printf '\n全部通过 ✅\n'
else
  printf '\n有失败项 ❌ —— 先修，不要提交、不要让 AI 说"完成"\n'
fi
exit "$FAIL"
