#!/usr/bin/env bash
# 无人值守跑任务卡：一张卡 = 一个干净会话（上下文不互相污染）
#
# 用法：
#   bash scripts/run-card.sh 00                 # 跑卡 00
#   bash scripts/run-card.sh 01 02 03           # 顺序跑多张
#   bash scripts/run-card.sh 05 --dry-run       # 只打印将要执行的命令，不真跑
#   MODEL=deepseek-reasoner bash scripts/run-card.sh 10   # 换更强模型跑关键卡
#   TURNS=80 BUDGET=2400 bash scripts/run-card.sh 15      # 放宽轮次/时长
#
# 说明：
#  - 每张卡一个独立进程，.hermes.md 会自动作为项目规则加载（cwd = 仓库根）。
#  - 默认开启 --yolo（自动批准工具调用），否则无人值守会在审批处卡死。
#     不想自动批准：NO_YOLO=1 bash scripts/run-card.sh 05
#  - 日志写到 logs/card-<编号>-<时间>.log，跑完自动跑一次 verify.sh。
#  - 脚本**不会**自动 git commit：人看过 diff 再提交。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

MODEL="${MODEL:-}"
TURNS="${TURNS:-60}"
BUDGET="${BUDGET:-1800}"
NO_YOLO="${NO_YOLO:-0}"
DRY=0
CARDS=()

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    ''|*[!0-9]*) echo "无法识别的参数：$a（只接受卡号或 --dry-run）"; exit 2 ;;
    *) CARDS+=("$(printf '%02d' "$a")") ;;
  esac
done

if [ "${#CARDS[@]}" -eq 0 ]; then
  echo "用法：bash scripts/run-card.sh <卡号> [卡号...] [--dry-run]"
  echo "现有卡片："; ls docs/cards/card-*.md 2>/dev/null | sed 's|.*/card-||; s|\.md$||' | tr '\n' ' '; echo
  exit 2
fi

command -v hermes >/dev/null 2>&1 || { echo "找不到 hermes 命令（先确认 hermes 在 PATH 里）"; exit 1; }
mkdir -p logs

for c in "${CARDS[@]}"; do
  f="docs/cards/card-${c}.md"
  if [ ! -f "$f" ]; then echo "❌ 找不到 $f"; exit 1; fi
  title="$(sed -n 's/^【任务卡 #\([0-9]*\)】//p' "$f" | head -1)"

  CMD=(hermes chat --query-file "$f" --oneshot -Q --in "$PWD" --max-turns "$TURNS" --run-budget "$BUDGET")
  [ -n "$MODEL" ] && CMD+=(-m "$MODEL")
  [ "$NO_YOLO" = "0" ] && CMD+=(--yolo)

  echo
  echo "════ 卡 $c ：$title"
  echo "    命令： ${CMD[*]}"
  if [ "$DRY" -eq 1 ]; then continue; fi

  LOG="logs/card-${c}-$(date +%Y%m%d_%H%M%S).log"
  "${CMD[@]}" 2>&1 | tee "$LOG"
  rc="${PIPESTATUS[0]}"

  echo
  echo "──── 卡 $c 自动验收（verify.sh）"
  bash scripts/verify.sh 2>&1 | tee -a "$LOG"

  echo
  echo "──── 卡 $c 结果：agent 退出码=$rc，日志=$LOG"
  echo "     人工看 diff： git diff"
  echo "     通过后提交： git add -A && git commit -m \"card-${c}: ${title}\""
  echo "     要回滚：     git reset --hard HEAD~1"
done
