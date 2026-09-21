VERDICT: PASS
CARDS: card-20（主体 307530b）
REVIEWED_DIFF: 三个提交一并审（本审为提交后复核）。
  - `307530b` card-20 主体：T17 `add_payee`（tools/payee.py）+ `payee_add` 意图 + Streamlit 表单（agent/payee_flow.py 编排入口）；改 agent/classifier、agent/orchestrator、data/dao（+insert_payee）、interfaces/web/{app,components}.py、tools/{schemas,subscription}.py；新增 tests/test_tools_payee(128)/test_payee_flow(117)/test_web_payee_form(94)。
  - `ae6b81e` A+B：`POST /api/payee`（interfaces/api/app.py，渠道无关，共用 `_turn_payload`）+ 订阅回执去行话（agent/templates.py 与 tools/subscription.py 同措辞）；新增 tests/test_api_payee(84)/test_subscription_wording(38)。
  - `4ade9a2` C：库文件迁出代码目录（`DB_PATH=var/bank.db`，卷只挂 `/app/var`）——修「命名卷盖住 /app/data 致容器跑旧代码」；改 .dockerignore/.env.example/.gitignore/Dockerfile/data.{_dao_core,seed}.py/docker-compose.yml/interfaces/web/app.py/scripts.{docker-entrypoint.sh,verify.sh}；新增 tests/test_compose_mounts_data_only(61)。
门禁：`uv run pytest` → **1051 passed**；`bash scripts/verify.sh` → **6/6 全绿 rc=0**（独立复跑，非引用）。
CHECKED:
  - 铁律 ①（完整手机号不落库/不回显/不进日志审计）——**独立查库，不看截图**。工具层（temp 库）与编排层（`orchestrator.submit_payee`）两条路径各跑一遍完整 11 位号：`payee` 表新行 `phone='138****5678'`、`is_whitelist=0`、`last_used_ts=NULL`；`data`/`facts`/`message` 均无完整号；**捕获 DEBUG 级日志**亦无完整号（非法号走 `INVALID_ARGUMENT`，日志写明"按铁律 8 不打印取值"）。**真机容器库**（volume 内 `var/bank.db`）复核：`陈晓/139****2222`、`周verify/137****9999` 均脱敏、`is_whitelist=0`；`payee_add` 审计 10 条，`params_json={"intent":"payee_add","masked_phone":"137****9999","name":"周verify"}`——**审计只存脱敏号，全表无完整号**。✓
  - ② 渠道无关**真共用一份**：`interfaces/api/app.py` 的 `_turn_payload(turn)` 是唯一字段映射，`chat_payload` 与 `payee_payload` 都经它；`/api/payee` 调 `orchestrator.submit_payee`（= `payee_flow.submit_payee`，Streamlit 表单同一入口）。**变异抽查**：给 `_turn_payload` 加一个字段 → `/api/payee` 侧 test_api_payee **4 failed**、`/api/chat` 侧 `api_smoke` ② **FAIL**（`字段=…,'extra'`）——一处形状改动**两处同时变红**，证明共享为真。`/api/chat` 6 字段零变化由 `test_chat_endpoint_shape_is_unchanged` 钉住。✓
  - ③ 分层：`grep` 全量确认 `interfaces/**` **无 tools/data/guard/sqlite3 import**；16c AST 守卫按 `LAYER_DIRS=(INTERFACES, APP_DIR)` 全树扫。**变异抽查**：给 `interfaces/api/app.py`（本卡改过的文件）加 `import data` → `test_layer_tree_never_imports_tools_data_guard_or_sqlite3` **FAIL**（`interfaces\api\app.py: data`）。✓
  - ④ C 的修复点**真有牙**：**变异抽查**——把 `docker-compose.yml` 的卷 `bankdata:/app/var` 改回 `/app/data` → `tests/test_compose_mounts_data_only.py` **2 failed**（`服务 api 把卷挂到了代码目录 /app/data`、`服务 api 的卷应只挂 /app/var，实际 ['/app/data']`）。**真机容器**：`grep -c "def insert_payee" data/dao.py` = **1**、`DB_PATH=var/bank.db`、`var/bank.db` 存在。✓
  - ⑤ B 两处措辞一致**真会变红**：`tests/test_subscription_wording.py::test_template_and_tool_message_are_word_for_word_identical` 断言 `templates.render("subscription_list", facts) == result.message`。**变异抽查**：把 `agent/templates.py` 的 `T_SUBSCRIPTION_ZOMBIE` 末尾改一个字 → 该断言 **FAIL**。面向用户回执已去行话（`"僵尸" not in rendered`）+ W 来自 facts（`zombie_window_months`）。✓
  - 真机实测（独立复现，容器内）：`POST /api/chat "加个收款人"` → `intent=payee_add / tool_calls=[] / tier=L1 / executed=false` + 引导语；`POST /api/payee {"name":"周verify","phone":"13712349999"}` → `executed=true` + `已添加 周verify（137****9999）`；`POST /api/chat "给陈晓转 100 元"` → `tier=L2（需输入短信验证码）` + 风险提示「首次向该收款人转账」。库 `(陈晓,139****2222,0)` 与 analyst 给的完全一致。（注：analyst 样例里 ② 的「已添加 陈晓…」我复跑时命中**去重**文案——陈晓已在持久卷里；改用全新收款人即得「已添加」，语义正确。）
RISKS:
  1. **web 容器起不来 = 宿主端口冲突（环境，非代码缺陷）**：宿主有个 `python.exe`（PID 16756，疑似宿主侧的 `streamlit run interfaces/web/app.py`）占着 **8501**，而 compose 的 web 服务也发布 8501 → `banking-web` 停在 `Created`（`bind: Only one usage of each socket address`）。**API 容器不受影响（healthy）**，analyst 的真机用例全在 API 侧，故本卡 PASS 不受阻。但现场要留意：**别同时跑宿主 streamlit 与 `docker compose up`**（二选一是同一 UI 的两种跑法）；确实要并存就改 compose 的 published 端口。我未杀该宿主进程（可能是用户正在跑的演示）。
  2. **去重让「已添加」变「已经在您的收款人里了」**：同库重复提交同名同号返回既有行（`ok=True`、`data` 仍 3 键、`message` 说明）——设计如此，但现场连点两次会看到不同文案，讲解时别误当 bug。
  3. **`bankdata` 卷跨版本持久**：卷里的库可能是旧版 seed 建的（entrypoint 只在库**不存在**时重建）。card-20 的 `insert_payee` 不依赖 seed 结构变化，故无碍；但**将来若改 seed/schema**，旧卷会带旧结构（靠 `init_db` 的 SchemaDriftError 兜底，非静默）。
  4. **`payee_add` 的 L1 档位是 agent 层显式传值**：`guard/` 的 `INTENT_BASE_TIERS` 没有 `payee_add`，`agent/payee_flow.py` 把 §5 的 `"L1"` 直接交给 `guard.permission.assess_write(tier=...)`——判档内核仍是 guard 一份，但**这个取值是 agent 侧的单点**；§5 若改动需同步 `PAYEE_ADD_TIER`。
  5. **手机号只认 11 位数字或已脱敏形式**：`+86`/带分隔符/国际号段会被 `INVALID_ARGUMENT`（demo 口径，错误消息不回显取值）；若将来做真实 H5 表单，前端需先归一化。
  6. **`_existing()` 靠 `dao.find_payee` 的模糊检索再收成精确比较**：去重正确（我验过：同名同号不重复、同号异名新建、他人的同名同号不挡本人），但检索是子串匹配，收款人多了以后是 O(n) 扫描——量级无关紧要，记此备查。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest` → **1051 passed**；`bash scripts/verify.sh` → 6/6、rc=0
  - 铁律①：temp 库 → `payee` 新行 `138****5678`、audit `params_json` 只含脱敏号、日志无完整号；容器库 → `陈晓/139****2222`、`周verify/137****9999`、`is_whitelist=0`、`payee_add` 审计 10 条全脱敏、`含完整号=False`
  - 变异②（`_turn_payload` 加字段）→ `/api/payee` 4 failed + `api_smoke` ② FAIL（两处同红）
  - 变异③（`interfaces/api/app.py` 加 `import data`）→ 16c 守卫 FAIL（`interfaces\api\app.py: data`）
  - 变异④（卷改回 `/app/data`）→ C 守卫 2 failed；容器 `grep -c "def insert_payee" data/dao.py` = 1
  - 变异⑤（templates 措辞改一字）→ 逐字一致断言 FAIL
  - 真机：`/api/chat "加个收款人"`→payee_add/L1/false；`/api/payee 周verify`→executed=true/「已添加」；`/api/chat "给陈晓转 100 元"`→L2/OTP/「首次向该收款人转账」
  - 容器内卡 20 单测（web 表单 + api/payee + tools/payee + payee_flow）→ **31 passed**
  - 全部变异已还原：`grep -rn "变异 M"` 无残留、`git status` 干净
VERDICT_REASON: 五条重点全过——完整手机号在**独立查的** payee/audit/日志三处均不落不明文（工具层与编排层两路径 + 真机容器库）、`/api/payee` 与 `/api/chat` 真共用一份 `_turn_payload`（改一处两处同红）且 6 字段未变、`interfaces/**` 零越层（守卫覆盖本卡改动文件）、C 的卷守卫有牙且容器内 `insert_payee` 唯一、B 的逐字一致断言会红；真机 API 三段行为与 analyst 记录一致、容器内 31 条卡 20 单测全过、门禁 1051/6-6 零回归。无 MUST_FIX；余 6 条为端口冲突/去重文案/卷持久/档位单点等非阻塞提示。
注：`banking-web` 因宿主 8501 被占停在 `Created`（环境冲突，非代码）；`banking-api` healthy 且跑的就是本次新构建镜像。我未动宿主进程；现场只需关掉宿主 streamlit 再 `docker compose up`，或改 compose 的 published 端口。
