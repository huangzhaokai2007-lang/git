#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  三人小组编排器 v2：打工的 → 审核师（门禁）→ 分析师（决策）
#
#  v2 修复（card-02 事故复盘）：
#   1. 范围门禁：卡文件「范围：」之外的文件被改动 → 直接 FAIL，不提交
#   2. 精准提交：只 git add 卡范围内的文件，绝不用 git add -A
#   3. 记账单独提交：board/ 由编排器提交，不混进卡的提交
#   4. 指纹门禁：审核师前后工作区指纹不一致 → 判定越权，BLOCKED 交给人
#   5. verify 前置：单测红就不浪费审核师一轮
#   6. 直接驱动三个 Bot（桌面 Bots 里能看到谁在忙）
#
#  用法：
#    bash scripts/agency.sh 03                 # 一张卡走完整流程
#    bash scripts/agency.sh 03 04              # 顺序多张
#    bash scripts/agency.sh --auto 03          # 分析师决定后续（最多 MAX_CARDS 张）
#    bash scripts/agency.sh 03 --dry-run       # 只看会做什么
#    USE_PROFILES=0 bash scripts/agency.sh 03  # 不用 Bot profile，退回默认 profile
# ─────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

WORKER_MODEL="${WORKER_MODEL:-deepseek-flash}"
REVIEWER_MODEL="${REVIEWER_MODEL:-deepseek-v4-pro}"
ANALYST_MODEL="${ANALYST_MODEL:-deepseek-v4-pro}"
MAX_FIX="${MAX_FIX:-2}"
MAX_CARDS="${MAX_CARDS:-6}"
TURNS="${TURNS:-60}"
BUDGET="${BUDGET:-1800}"
USE_PROFILES="${USE_PROFILES:-1}"
DRY=0; AUTO=0; CARDS=()

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --auto) AUTO=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    ''|*[!0-9]*) echo "无法识别的参数：$a"; exit 2 ;;
    *) CARDS+=("$(printf '%02d' "$a")") ;;
  esac
done
[ "${#CARDS[@]}" -eq 0 ] && { echo "用法：bash scripts/agency.sh <卡号...> [--auto] [--dry-run]"; exit 2; }
mkdir -p board/reviews logs board/.tmp

# ── 工具函数 ─────────────────────────────────────────────────
profile_ok() { [ "$USE_PROFILES" = "1" ] && hermes profile list 2>/dev/null | grep -qE "[[:space:]]${1}[[:space:]]"; }

run_agent() {   # $1=角色profile $2=prompt文件 $3=模型 $4=日志
  local -a cmd=(hermes)
  profile_ok "$1" && cmd+=(-p "$1")
  cmd+=(chat --query-file "$2" --oneshot -Q --in "$PWD" --max-turns "$TURNS" --run-budget "$BUDGET" -m "$3" --yolo)
  "${cmd[@]}" 2>&1 | tee "$4"
}

build_prompt() { # $1=角色文件 $2=输出 ; 其余=追加材料
  local role="$1" out="$2"; shift 2
  cat "$role" > "$out"
  for f in "$@"; do
    printf '\n\n=== 以下是本次材料：%s ===\n' "$f" >> "$out"; cat "$f" >> "$out"
  done
}

card_scope() {  # 解析卡文件「范围：」行 → 允许路径，一行一个
  sed -n 's/^范围：//p' "$1" | head -1 | tr '、,，' '\n' \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$'
}

changed_files() { git status --porcelain -uall | sed 's/^...//' | tr -d '"'; }

# 编排器自己的地盘（账本/裁决/日志/临时），既不算越界，也不进卡的提交
is_housekeeping() { case "$1" in board/*|logs/*) return 0 ;; *) return 1 ;; esac; }

in_scope() {  # $1=文件 $2=卡文件
  local f="$1" p
  while IFS= read -r p; do
    case "$p" in
      */) case "$f" in "$p"*) return 0 ;; esac ;;    # 目录 → 前缀匹配
      *)  [ "$f" = "$p" ] && return 0 ;;              # 文件 → 精确匹配
    esac
  done < <(card_scope "$2")
  return 1
}

out_of_scope() {  # 列出越界文件
  local f
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    is_housekeeping "$f" && continue
    in_scope "$f" "$1" || echo "$f"
  done < <(changed_files)
}

sig() { { git status --porcelain -uall; git diff HEAD; } | sha256sum | cut -c1-12; }

run_verify() { bash scripts/verify.sh 2>&1; }

# ── 单张卡 ───────────────────────────────────────────────────
# 返回 0=通过 1=重做已用尽 2=需要人
run_one_card() {
  local c="$1" CARD="docs/cards/card-${c}.md" try=1 verdict=""
  [ -f "$CARD" ] || { echo "❌ 找不到 $CARD"; return 2; }
  local title; title="$(sed -n 's/^【任务卡 #\([0-9]*\)】//p' "$CARD" | head -1)"
  echo; echo "══════════ 卡 $c ：$title"
  echo "           允许改动的范围： $(card_scope "$CARD" | tr '\n' ' ')"

  while [ "$try" -le $((MAX_FIX+1)) ]; do
    # —— 打工的 ——
    echo; echo "──── [卡 $c] 打工的（$WORKER_MODEL）第 $try 轮"
    local WLOG="logs/card-${c}-worker-try${try}.log" P="board/.tmp/prompt-worker-${c}-${try}.md"
    build_prompt agents/worker.md "$P" "$CARD"
    if [ "$try" -gt 1 ]; then
      printf '\n\n=== 上一轮未通过，必须逐条闭环（不许改测试来过关）。越界文件必须 git checkout 回退 ===\n' >> "$P"
      cat "board/reviews/card-${c}.md" >> "$P"
    fi
    if [ "$DRY" -eq 1 ]; then echo "     [dry-run] hermes -p worker chat -m $WORKER_MODEL < $P"; else
      run_agent worker "$P" "$WORKER_MODEL" "$WLOG" | tail -20
    fi

    # —— 范围门禁（先于审核，省一轮）——
    local OOS; OOS="$(out_of_scope "$CARD")"
    if [ -n "$OOS" ]; then
      echo "     ⛔ 范围门禁：以下文件不在卡范围内，必须先回退或说明："
      echo "$OOS" | sed 's/^/        - /'
      { echo "VERDICT: FAIL"; echo "CARDS: card-${c}"; echo "CHECKED:"; echo "  - 越界改动：发现卡范围外改动"; \
        echo "MUST_FIX:"; echo "$OOS" | sed 's/^/  1. 回退 /'; \
        echo "EVIDENCE:"; echo "  - \`git status --porcelain -uall\` 列出上述越界路径（编排器范围门禁，未经人/AI 审核）"; \
        echo "VERDICT_REASON: 编排器范围门禁拦截：卡外文件被改动。"; } > "board/reviews/card-${c}.md"
      try=$((try+1)); continue
    fi

    # —— verify 前置 ——
    echo "     ---- 自动验收"
    local VOUT; VOUT="$(run_verify)"; echo "$VOUT" | tail -10 | tee -a "$WLOG"
    if [ "$DRY" -eq 0 ] && ! echo "$VOUT" | grep -q "全部通过"; then
      echo "     ❌ verify 未全绿 → 不进审核，直接重做"
      { echo "VERDICT: FAIL"; echo "CARDS: card-${c}"; echo "MUST_FIX:"; echo "  1. 让 \`bash scripts/verify.sh\` 全绿（当前有失败项）"; \
        echo "EVIDENCE:"; echo "$VOUT" | tail -12; echo "VERDICT_REASON: 编排器 verify 前置门禁未通过。"; } > "board/reviews/card-${c}.md"
      try=$((try+1)); continue
    fi

    # —— 审核师（带指纹门禁）——
    echo; echo "──── [卡 $c] 审核师（$REVIEWER_MODEL）"
    local RLOG="logs/card-${c}-review-try${try}.log" RP="board/.tmp/prompt-review-${c}-${try}.md"
    if [ "$DRY" -eq 1 ]; then echo "     [dry-run] hermes -p reviewer chat -m $REVIEWER_MODEL < $RP"; verdict="PASS"; else
      { echo "=== 当前未提交改动（自己再跑 git diff HEAD 看细节）==="; git status --short -uall; } > /tmp/gs.$c.tmp
      build_prompt agents/reviewer.md "$RP" "$CARD" /tmp/gs.$c.tmp
      local PRE; PRE="$(sig)"
      run_agent reviewer "$RP" "$REVIEWER_MODEL" "$RLOG" | tail -28
      local POST; POST="$(sig)"
      sed -n '/^VERDICT:/,$p' "$RLOG" > "board/reviews/card-${c}.md"
      verdict="$(grep -m1 -E '^VERDICT:' "$RLOG" | sed -E 's/^VERDICT:[[:space:]]*//' | tr -d '\r' | awk '{print $1}' | tr '[:lower:]' '[:upper:]')"
      if [ "$PRE" != "$POST" ]; then
        echo "     🚨 指纹不一致（$PRE -> $POST）：审核师动了工作区 → 本轮裁决作废"
        { echo "VERDICT: BLOCKED"; echo "CARDS: card-${c}"; \
          echo "MUST_FIX:"; echo "  1. 人工检查 \`git status --porcelain -uall\` 与 \`git diff HEAD\`：审核师违反了只读约束，需判定哪些改动是打工的、哪些是审核师多手。"; \
          echo "EVIDENCE:"; echo "  - 编排器指纹门禁：审核前 $PRE，审核后 $POST（不一致）"; \
          echo "VERDICT_REASON: 审核师改动工作区，违反只读约束。"; } >> "board/reviews/card-${c}.md"
        return 2
      fi
    fi
    echo "     >>> 裁决：${verdict:-<解析不出=未通过>}"

    case "$verdict" in
      PASS)
        if [ "$DRY" -eq 1 ]; then echo "     [dry-run] git add <卡范围文件> && commit"; return 0; fi
        # 只提交卡范围内的文件
        local -a add=()
        while IFS= read -r f; do
          [ -z "$f" ] && continue
          is_housekeeping "$f" && continue
          in_scope "$f" "$CARD" && add+=("$f")
        done < <(changed_files)
        if [ "${#add[@]}" -gt 0 ]; then
          git add -- "${add[@]}"
          git -c user.email=team@local -c user.name=team commit -q -m "card-${c}: ${title}" \
            && echo "     ✓ 提交（$(printf '%s ' "${add[@]}")）"
        else
          echo "     ⚠ 卡范围内没有改动可提交（可能上一轮已提交）"
        fi
        # 记账单独提交
        if ! git diff --quiet --cached 2>/dev/null || ! git diff --quiet || [ -n "$(git status --porcelain -uall -- board/ logs/ | grep -v 'board/.tmp')" ]; then
          git add -- board/ 2>/dev/null
          git -c user.email=team@local -c user.name=team commit -q -m "board: card-${c} 记账（审核 ${verdict}）" 2>/dev/null \
            && echo "     ✓ 记账提交（board/）"
        fi
        return 0 ;;
      BLOCKED)
        echo "     🛑 审核师 BLOCKED —— 需要人类决策。见 board/reviews/card-${c}.md"
        return 2 ;;
      *)
        echo "     ❌ 未通过（第 $try 轮）。修复清单：board/reviews/card-${c}.md"
        try=$((try+1)) ;;
    esac
  done
  echo "     🛑 卡 $c 连续 $MAX_FIX 轮未过 —— 交给人。建议：换模型 / 拆卡 / 人工介入"
  return 1
}

run_analyst() {
  local c="$1" L="logs/analyst-after-${c}.log"
  echo; echo "──── 分析师（$ANALYST_MODEL）复盘并决定下一步"
  git log --oneline -15 > /tmp/gitlog.tmp 2>&1
  ls board/reviews/ > /tmp/rl.tmp 2>/dev/null
  build_prompt agents/analyst.md board/.tmp/prompt-analyst.md board/ledger.md /tmp/gitlog.tmp /tmp/rl.tmp
  if [ "$DRY" -eq 1 ]; then echo "     [dry-run] hermes -p analyst chat -m $ANALYST_MODEL < board/.tmp/prompt-analyst.md"; return 0; fi
  local OUT; OUT="$(run_agent analyst board/.tmp/prompt-analyst.md "$ANALYST_MODEL" "$L" | tail -40)"
  printf '\n%s\n' "$OUT" >> board/ledger.md
  echo "$OUT" | sed -n 's/^NEXT_CARD:[[:space:]]*//p' | head -1 | tr -dc '0-9' > /tmp/next.tmp
  echo "$OUT" | sed -n 's/^MODEL:[[:space:]]*//p' | head -1 | tr -d '\r ' > /tmp/model.tmp
  echo "$OUT" | sed -n 's/^ACTION:[[:space:]]*//p' | head -1 | tr -d '\r ' > /tmp/action.tmp
  echo "     >>> NEXT_CARD=${NEXT:-$(cat /tmp/next.tmp 2>/dev/null)} MODEL=$(cat /tmp/model.tmp 2>/dev/null) ACTION=$(cat /tmp/action.tmp 2>/dev/null)"
  git add -- board/ 2>/dev/null
  git -c user.email=team@local -c user.name=team commit -q -m "board: card-${c} 分析师决策" 2>/dev/null && echo "     ✓ 账本已提交"
}

# ── 主循环 ───────────────────────────────────────────────────
for c in "${CARDS[@]}"; do
  [ -f "docs/cards/card-${c}.md" ] || { echo "❌ 找不到 docs/cards/card-${c}.md"; exit 1; }
  run_one_card "$c"; rc=$?
  run_analyst "$c"
  if [ "$rc" -ne 0 ]; then echo; echo "🛑 停在卡 $c —— 人工处理后继续。"; exit 1; fi
done

if [ "$AUTO" -eq 1 ] && [ "$DRY" -eq 0 ]; then
  n=0
  while [ "$n" -lt "$MAX_CARDS" ]; do
    n=$((n+1))
    NEXT="$(cat /tmp/next.tmp 2>/dev/null | tr -dc '0-9')"
    ACT="$(cat /tmp/action.tmp 2>/dev/null | tr -d ' \r')"
    MOD="$(cat /tmp/model.tmp 2>/dev/null | tr -d ' \r')"
    echo; echo "══ 分析师决定：NEXT_CARD=${NEXT:-none} MODEL=${MOD:-默认} ACTION=${ACT:-run}"
    { [ -z "$NEXT" ] || [ "$NEXT" = "none" ]; } && { echo "✔ 分析师判定：没有下一张卡，收工"; break; }
    case "$ACT" in human|rollback|fix_first) echo "🛑 分析师要求人工介入（$ACT），已停止。"; break ;; esac
    [ -n "$MOD" ] && WORKER_MODEL="$MOD"
    run_one_card "$(printf '%02d' "$NEXT")" || { echo "🛑 卡 $NEXT 未过，停止。"; break; }
    run_analyst "$(printf '%02d' "$NEXT")"
  done
fi

echo; echo "══ 本轮结束。账本 board/ledger.md ─ 裁决 board/reviews/ ─ 日志 logs/"
