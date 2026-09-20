# 2026 FinTechathon · AI Banking Agent —— 模拟银行环境（全合成数据，不连接任何真实银行）
#
# 离线可用：
#   - 依赖由 `uv.lock` 冻结并在**构建期**装进镜像（构建需要外网，运行不需要）；
#   - 合成数据库在构建期烘焙；容器首次启动若挂载卷为空，入口脚本会本地重建（`scripts/docker-entrypoint.sh`）；
#   - 没有 `LLM_API_KEY` 也能起：意图识别降级为"追问/未接通"，业务数字一律来自本地库（铁律 2）。
# 容器里也能一键验收：`docker compose exec api bash scripts/verify.sh`（dev 组已装，全程离线）。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DB_PATH=var/bank.db \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    TZ=Asia/Shanghai

WORKDIR /app

# 0) 时区数据 + 钉住进程时区（**必须**，否则业务判定会反）：
#    业务时间走 `datetime.now()`（= 进程本地时间）—— `tools/transfer._now()` 的 night(23:00–06:00) 降级因子、
#    「单日累计」的日界、确认凭证与订阅生效的 TTL、审计与回执里的时间戳，全都读它。
#    容器默认 UTC：CST 白天 11:54 会被算成 UTC 03:54 → 误判「夜间」升档；CST 真夜间 23:00 = UTC 15:00 → 反而判白天。
#    `python:3.11-slim` 不带 /usr/share/zoneinfo，所以显式装 tzdata（构建期联网，运行期依旧不需要外网）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# 1) 冻结依赖（**不** --no-dev：容器内要能跑 scripts/verify.sh 的一键验收）
RUN pip install --no-cache-dir "uv==0.12.10"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --quiet

# 2) 源码（.dockerignore 已排除 .venv / .git / .env / 本地库 / 缓存）
#    注（卡 20-C）：库文件住 `var/`（数据目录），**不在** `data/`（代码目录）里 —— 见 docker-compose.yml 里
#    「卷只能挂数据目录」那条注释的来龙去脉。
COPY . .

# 3) 烘焙合成数据（全合成、本地生成，运行期不再需要网络）
#    写到 `$DB_PATH`（= var/bank.db）；父目录由 data/db.py 的 init_db/reset_db 自动创建，这里不必手动 mkdir。
#    compose 场景下 /app/var 被命名卷盖住（首启为空）→ scripts/docker-entrypoint.sh 会在卷里重建一份。
RUN python -m data.seed --reset

EXPOSE 8000 8501
# 健康检查**故意不写在镜像里**：本镜像被两个服务共用（评测入口 8000 / 网页端 8501），
# 在镜像里写死一个端口的全局探针，另一个服务会继承到错误端口并永远 unhealthy（真机已踩）。
# 探针由编排层按服务定义 —— 见 docker-compose.yml 里每个 service 的 healthcheck。

ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
# 默认起「评测入口」（POST /api/chat）；网页端是同一镜像的另一个服务，见 docker-compose.yml
CMD ["python", "-m", "interfaces.api"]
