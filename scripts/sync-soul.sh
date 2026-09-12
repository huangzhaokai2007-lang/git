#!/usr/bin/env bash
# 把仓库里的角色定义同步成三个 Bot 的 SOUL.md（角色真相在仓库，不在 profile 里）
# 用法：bash scripts/sync-soul.sh [analyst|worker|reviewer ...]   默认三个都同步
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
H="${LOCALAPPDATA:-$HOME/AppData/Local}/hermes"
REPO_ABS="$(pwd -W 2>/dev/null || pwd)"

names=("$@")
[ "${#names[@]}" -eq 0 ] && names=(analyst worker reviewer)

for n in "${names[@]}"; do
  src="agents/${n}.md"
  dst="$H/profiles/$n/SOUL.md"
  [ -f "$src" ] || { echo "❌ 找不到 $src"; continue; }
  [ -d "$H/profiles/$n" ] || { echo "❌ 没有 profile $n（先 hermes profile create $n）"; continue; }
  case "$n" in
    analyst)  cn="分析师（团队大脑）" ;;
    worker)   cn="打工的（实现者）" ;;
    reviewer) cn="审核师（审计者）" ;;
    *)        cn="$n" ;;
  esac
  {
    echo "# 我是：$cn"
    echo
    echo "所属：FinTechathon 2026 AI Banking Agent 三人小组（分析师 / 打工的 / 审核师）"
    echo "项目仓库：$REPO_ABS"
    echo
    echo "> 本文件由仓库内 \`agents/$n.md\` 同步而来。**源文件是唯一真相**——改角色请改源文件，再跑 \`bash scripts/sync-soul.sh\`。"
    echo
    cat "$src"
  } > "$dst"
  echo "✓ $n  <-  $src  ($(wc -c < "$dst") 字节)"
done
echo "同步完成。桌面端 Bots 标签里对应 Bot 的身份即刻生效（新会话）。"
