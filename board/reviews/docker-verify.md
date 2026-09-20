VERDICT: PASS
SCOPE: Docker 全链路独立复验（card-18 RISK①「Docker 未真机构建」的最后一步闭环）。环境：Docker v29.8.0（WSL2），镜像 `ai-banking-agent:latest` 由当前工作树（HEAD 4842941）构建。
CHECKED:
  - ① 两容器健康：`docker compose ps` → **banking-api (healthy)** + **banking-web (healthy)**（此前 web 因全局探针端口错而永远 unhealthy，已修）。Dockerfile 里已**删除全局 HEALTHCHECK**（改为注释说明），探针**按 service** 定义在 docker-compose.yml：api → `GET :8000/healthz`、web → `GET :8501/_stcore/health`。
  - ② 容器内一键验收：`docker compose exec -T api bash scripts/verify.sh` → **6/6 全绿、rc=0**（2/6 单测 1013 passed；3/6 用例 32 passed；4/6 冒烟 `断言通过：回执含 2026-08`；5/6 红线 100 passed；6/6 红队 + IM 7/7 + 评测入口冒烟 4/4）。
  - ③ 评测入口契约（真机 HTTP）：`curl -X POST http://localhost:8000/api/chat`（UTF-8 文件体 `{"text":"查一下余额"}`）→ 字段**恰好 6 个** `(reply, intent, tool_calls, tier, executed, trace_id)`；`intent=balance_query`、`tool_calls=['get_balance']`、`tier=null`、`executed=false`；`reply` 含**事实包**余额 `46,634.00`。（直接 `-d '…'` 会因 shell 编码 body parse error —— 已按 UTF-8 文件传，复现了 analyst 的提示。）
  - ④ 变异抽查（探针真按端口判）：把 web 的 healthcheck 端口 `8501`→`8000` → `docker compose up -d web` → 约 130s 后 `banking-web` 转 **unhealthy**（`FailingStreak=7`、探针 exit=1 连接错误），api 仍 healthy。还原 `8501` + 重建 → web 回 **healthy**。`git status` 干净（改动无残留）。
  - 附带厘清：我早前引用的「997 单测 / 84 红线」是**旧数**——仓库已提交 `984c75d` 把文档按实测对齐（`84→100` / `997→1013`）。本次宿主机与容器**都**是 1013 passed / 段 5 = 100 passed，且关键文件 sha256 两侧**逐一相同** → 镜像非陈旧，容器测的就是当前代码。
RISKS:
  1. **构建期需要外网**（`uv sync --frozen` 装依赖）；运行期离线。若评测环境完全断网且不允许预构建镜像，需先在有网机器 `docker save` 分发镜像 —— 非本卡范围，但别把它当成"完全离线可构建"。
  2. **容器继承了宿主的 `.env`**（compose `env_file: .env, required:false`）：本机有 `LLM_API_KEY` 时，`/api/chat` 的 `reply` 会被大模型**润色**（措辞随模型变；本例实测是「您储蓄账户的余额是 …」），而**数字仍来自事实包**。评分/程序化断言请钉**6 个字段与数字**，不要比 `reply` 原文；要完全确定性的措辞就清空 `LLM_API_KEY`。
  3. 首次启动健康判定有延迟：web `start_period 30s + retries 5 × interval 20s` ≈ **130s** 才出 healthy/unhealthy 结论（api 为 30s + 3×20s）。`docker compose up -d` 后立刻 `ps` 会看到 `health: starting`，属正常，不是失败。
  4. 镜像内 `COPY . .` 会把 `docs/`、`tests/` 一并带入（`.dockerignore` 已排 `.env*`/`.git`/`.venv`/本地库/缓存）—— 体积略大但便于容器内跑 verify，属有意取舍。
MUST_FIX: 无
EVIDENCE:
  - `docker compose ps` → `banking-api … (healthy)` + `banking-web … (healthy)`
  - `docker compose exec -T api bash scripts/verify.sh` → 1013 / 32 / 断言含 2026-08 / 100 / 红队+IM+冒烟全 OK → `全部通过 ✅`、rc=0
  - `curl … --data-binary @api_chat_body.json` → `{"reply":"…46,634.00 元…","intent":"balance_query","tool_calls":["get_balance"],"tier":null,"executed":false,"trace_id":"trace-…"}`；Python 复核「恰好 6 字段 = True」「回执含事实包余额 = True」
  - 变异：web 探针端口 8501→8000 → `banking-web (unhealthy)`、`FailingStreak=7`、exit=1；还原后 → `banking-web (healthy)`
  - 一致性：镜像内 `tests/test_injection.py` == 宿主机 sha256（58 收集）；`tests/test_facts_check.py` == （42 收集）；`guard/injection.py`、`guard/facts_check.py`、`scripts/verify.sh` 逐一相同
  - `git status --short` → 空（变异已还原）
VERDICT_REASON: 四项独立复跑全部成立——两容器均 healthy、容器内 verify 6/6 全绿、`/api/chat` 恰好 6 字段且数字来自事实包、把 web 探针端口改错即真变 unhealthy（还原后恢复）；镜像与当前代码哈希一致（非陈旧），card-18 的「Docker 未真机构建」RISK 由此闭环。无 MUST_FIX；余 4 条为部署/评测口径类非阻塞提示。
