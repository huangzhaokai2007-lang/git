VERDICT: PASS
CARDS: card-18
REVIEWED_DIFF: 已提交 6a67a7a（11 文件，+855 行），本审为提交后复核：README.md(267)、docs/03-运行与评测.md(127)、Dockerfile(38)、docker-compose.yml(54)、.dockerignore(13)、scripts/bootstrap.sh(35)、scripts/docker-entrypoint.sh(14)、scripts/api_smoke.py(127)、scripts/verify.sh(81，5→6 段)、interfaces/api/app.py(60)、interfaces/api/__main__.py(39)。基线 970 passed（未破）。
CHECKED:
  - 接口一致性（红线 2）：`RESPONSE_FIELDS = ("reply","intent","tool_calls","tier","executed","trace_id")`，`chat_payload` 走**同一个** `orchestrator.handle(text, session_id=…)` 后**只**落这 6 个字段；`orchestrator.handle` 签名 `(text, *, history=None, clarify_round=0, session_id=None)` 与调用一致（session_id 可省）。`ChatIn` `extra="forbid"`。冒烟断言 `tuple(body) == RESPONSE_FIELDS`（**顺序也钉**）。
  - 越界改动：无。`interfaces/api/**` import = stdlib + fastapi + pydantic + `agent.orchestrator`（**未 import guard/data/tools**，独立 grep 确认）；`__main__` 的 uvicorn/create_app 懒加载在 main() 内，import 阶段无副作用。
  - 测试真实性（**2 个变异全真报警**，均已还原）：① 给 `chat_payload` 返回值多加一个字段 `"extra"` → api_smoke ② FAIL（`字段=…,'extra'`，3/4）；② 给 `interfaces/api/app.py` 加 `import data` → 16c 守卫 `test_interface_tree_never_imports_tools_data_guard_or_sqlite3` FAIL（`app.py: data`）。
  - 边界与异常（红线 3）：verify 现 **6 段**，第 6 段 = redteam + `interfaces.im --selftest` + `scripts/api_smoke.py`，**全部离线、不占端口、不需要 LLM key**。独立复跑：verify rc=0（1/6 环境、2/6 单测 970 passed、3/6 用例 32 passed、4/6 SKIP、5/6 红线 84 passed、6/6 红队 rc0 + IM 7/7 + 冒烟 4/4）；`scripts/api_smoke.py` rc=0、`scripts/redteam.py` rc=0。冒烟四项实测：①/healthz 200；②契约 6 字段 + 真跑 get_balance + 金额来自事实包；③注入→unsafe_request、零 LLM 调用；④带 session_id 写路径→tier=L2、executed=false、tool_calls=['preview_transfer']。
  - 权限/审计/安全（红线 4/5）：Dockerfile 构建期 `uv sync --frozen` + `python -m data.seed --reset`（合成库烘焙进镜像）→ **运行期不需要外网**；无 `LLM_API_KEY` 也能起（意图识别降级、业务数字仍来自本地库，铁律 2）；入口脚本卷空则本地重建（`set -euo pipefail`）。`.dockerignore` 排除 `.venv/.git/.env/data/*.db/**__pycache__/board/agents`；**独立密钥扫描**（README/docs03/Dockerfile/compose/两个 sh/api_smoke/interfaces/api）**未发现硬编码密钥**；compose `env_file: .env (required:false)`，不硬编码。README §5 工具表 **16 行 T1–T16**，并**如实加脚注**：§2 标题写「15 个」而表内 T1–T16，「计数差异请人类拍板（改标题或改表，属规格变更）」。
RISKS:
  1. **Docker 未真机构建**（worker 待拍板）：Dockerfile/compose/entrypoint 逻辑我只做了**静态审**（本环境无 Docker），真机构建可能踩坑（uv 版本 0.12.10、uv.lock `--frozen`、镜像内 data.seed、HEALTHCHECK）。建议交付前在有 Docker 的机器跑一次 `docker compose up --build` + `docker compose exec api bash scripts/verify.sh`。
  2. **SPEC-CHANGE 待人类**：规格 §2 标题「（15 个…）」与表内 T1–T16 不一致（README 已脚注、未擅改规格）。需人类拍板「改标题为 16」还是「T16 独立成节」。
  3. verify **第 4 段 SKIP**（`app/cli.py 未实现`）：6 段里唯一没跑起来的一段；已见 card-19 落 `app/cli.py`（审核时未跟踪），补上后 4/6 才有牙。
  4. `.dockerignore` 只排 `.env`（未排 `.env.local`/`*.key`/`credentials*` 等变体）：`COPY . .` 若遇其它密钥文件会打进镜像。建议补 `*.env*` / `*.key` / `secrets*`。
  5. `session_id` 为**可选**加分项（规格未定义）：`/api/chat` 只给 `text` 也能跑（无多轮确认/验证码上下文）。口径已记文档，属可接受取舍。
  6. api_smoke / IM selftest 都用 **ASGI 直连**（不起 uvicorn）——离线确定性强，但没压实到真实 HTTP 栈（端口/并发）。demo 足够。
MUST_FIX: 无
EVIDENCE:
  - `bash scripts/verify.sh` → **rc=0、全部通过 ✅**（6 段；第 6 段红队 rc0 + IM 7/7 + 冒烟 4/4）
  - `uv run python scripts/api_smoke.py` → 4/4、rc=0（契约 6 字段 + 真跑工具 + 事实包金额）
  - `uv run pytest` → 970 passed；`uv run pytest tests/test_web_layering.py` → 32 passed（守卫覆盖 interfaces/api）
  - 变异①（多一个字段）→ 冒烟 ② FAIL；②（interfaces/api 加 `import data`）→ 16c 守卫 FAIL（均已还原，`grep 变异 M` 无残留）
  - 密钥扫描：8 个新增文件 + interfaces/api 两文件 → 无硬编码密钥
VERDICT_REASON: 三条红线全过——分层干净（16c 守卫覆盖 interfaces/api，变异真红）、评测入口恰好 6 字段且复用同一 orchestrator（变异真红）、verify 6 段全离线 rc 可判；Dockerfile 离线可起 + .dockerignore 无密钥 + README 工具表 16 行并如实标注 §2 计数待人类拍板；970 基线零回归，无 MUST_FIX；仅「Docker 未真机构建」「§2 计数 SPEC-CHANGE」「verify 第 4 段 SKIP」等 6 条非阻塞 RISK。

注：审核期间检测到 **card-19（演示 + 答辩）并发进行**（未跟踪 app/cli.py + docs/答辩提纲.md + scripts/demo.py；已改 README.md），全量 pytest 970 passed。card-19 正在补 verify 第 4 段的 app/cli.py，与 card-18（已提交、970 绿）无关，请卡 19 收口后复核并复跑 verify。
