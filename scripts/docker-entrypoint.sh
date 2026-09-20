#!/usr/bin/env bash
# 容器入口（卡 18）：本地库不存在就**离线造一份合成数据**，然后把命令行交给 CMD。
# 为什么需要它：compose 把 /app/var（**数据目录**，库文件 var/bank.db）挂成共享卷（两个服务看到同一份账），
# 卷首次挂载是空的，于是由这里兜底建库 —— 与 scripts/verify.sh 第 1 段的兜底同一口径。
# 卡 20-C：卷**只**能挂数据目录。曾经挂的是 /app/data（代码目录），容器里跑的是第一次建卷时的旧代码，
# `--build` 重建镜像也救不了（卷盖在镜像之上）→ 网页端「加个收款人」必 500。详见 docker-compose.yml 注释。
set -euo pipefail

DB="${DB_PATH:-var/bank.db}"
if [ ! -f "$DB" ]; then
  echo "本地库不存在 → 造合成数据（离线、全合成）"
  # 两个服务可能同时首启：建库失败不致命，若另一个已经建好就直接继续（否则真退出）
  python -m data.seed --reset || { [ -f "$DB" ] || exit 1; }
fi

exec "$@"
