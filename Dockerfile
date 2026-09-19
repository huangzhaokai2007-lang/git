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
    DB_PATH=data/bank.db \
    API_HOST=0.0.0.0 \
    API_PORT=8000

WORKDIR /app

# 1) 冻结依赖（**不** --no-dev：容器内要能跑 scripts/verify.sh 的一键验收）
RUN pip install --no-cache-dir "uv==0.12.10"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --quiet

# 2) 源码（.dockerignore 已排除 .venv / .git / .env / 本地库 / 缓存）
COPY . .

# 3) 烘焙合成数据（全合成、本地生成，运行期不再需要网络）
RUN python -m data.seed --reset

EXPOSE 8000 8501
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
# 默认起「评测入口」（POST /api/chat）；网页端是同一镜像的另一个服务，见 docker-compose.yml
CMD ["python", "-m", "interfaces.api"]
