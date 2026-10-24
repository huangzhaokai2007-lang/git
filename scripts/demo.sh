#!/usr/bin/env bash
# 演示一键准备（B4）：重置合成库 → 打印演示台账 → 起服务
# 用法：bash scripts/demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/3 重置演示库（合成数据） =="
rm -f var/bank.db
uv run python -m data.seed --reset

echo
echo "== 2/3 演示台账（被问就报这些） =="
uv run python - <<'PY'
from data import dao
from tools import payee, transfer

facts = transfer.preview_transfer("payee_0003", 10_000).facts
print(f"单笔上限 {facts['limits_single_max_yuan']} 元 · 今日剩余 {facts['limits_daily_remaining_yuan']} 元")
print(f"夜间时段 {facts['night_from_hour']}:00 - {facts['night_to_hour']}:00（夜间是降级因子）")
print()
listing = payee.list_payees()
profile = listing.facts["profiles"]
for item in listing.facts["items"]:
    extra = profile[item["payee_id"]]
    kind = "白名单" if item["is_whitelist"] else ("黑名单" if extra["blacklist"] else "普通")
    quota = extra["whitelist_quota"]
    quota_txt = f"{quota / 100:.2f} 元/月" if quota else "—"
    print(f"{item['name']:6s} {item['masked_phone']}  {kind:4s} 额度 {quota_txt:12s} "
          f"有效期 {extra['whitelist_expire'] or '长期':10s} 标签 {','.join(extra['tags']) or '—'}")
print()
row = dao.connection().execute("SELECT balance, available FROM account WHERE type = 'savings'").fetchone()
print(f"储蓄余额 {row['balance'] / 100:,.2f} 元 · 可用 {row['available'] / 100:,.2f} 元")
PY

echo
echo "== 3/3 起服务：http://127.0.0.1:8501 =="
echo "   （Ctrl+C 结束；想重新来一遍就再跑一次本脚本）"
uv run streamlit run interfaces/web/app.py --server.port 8501
