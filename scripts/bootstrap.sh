#!/usr/bin/env bash
# 全新机器上的一条命令（卡 18）：装环境 → 造合成数据 → 一键验收 → 打印下一步。
#
#   bash scripts/bootstrap.sh           # 完整（约 1~2 分钟，全程离线）
#   bash scripts/bootstrap.sh --quick   # 只装环境 + 造数据，跳过验收
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

echo "== 0/4 环境自检 =="
if ! command -v uv >/dev/null 2>&1; then
  echo "   缺少 uv —— 安装：curl -LsSf https://astral.sh/uv/install.sh | sh  （或 pipx install uv）"
  exit 1
fi
echo "   uv: $(uv --version)"

echo "== 1/4 安装依赖（uv sync：Python 3.11 + 冻结版本）=="
uv sync || exit 1

echo "== 2/4 造合成数据（全合成，不连接任何真实银行）=="
uv run python -m data.seed --reset || exit 1

if [ "${1:-}" = "--quick" ]; then
  echo "== 3/4 跳过一键验收（--quick）=="
else
  echo "== 3/4 一键验收（离线：单测 + 评测用例 + 红线 + 红队 + 通道 + 评测入口）=="
  bash scripts/verify.sh || exit 1
fi

echo "== 4/4 起服务 =="
cat <<'TIP'
   网页端    uv run streamlit run interfaces/web/app.py   → http://127.0.0.1:8501
   评测入口  uv run python -m interfaces.api              → POST http://127.0.0.1:8000/api/chat
   IM 通道   uv run python -m interfaces.im               → POST http://127.0.0.1:8090/im/loopback
   容器      docker compose up --build                    → :8000 评测入口 / :8501 网页端
TIP
