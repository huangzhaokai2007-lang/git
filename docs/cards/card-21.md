<!-- 由分析师代人类建立（人类已批契约变更）；与 docs/02-AI指令剧本.md 的对应小节保持同步 -->

> 用法：`bash scripts/run-card.sh 21`（无人值守）或在 Hermes 里直接说「做卡 21」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #21】多用户注册/登录（设备识别 → 手机号注册 → 初始十万元）

第一步：读 `CLAUDE.md` 和 `docs/01-接口规格.md`，用 5 行复述你理解的约束，等我确认再动手。

范围：只允许改下列文件，其他一律不动。
- `docs/01-接口规格.md`（**人类已批的 SPEC-CHANGE**：见下「接口」两条）
- `data/schema.sql`（加 `device` 表）
- `tests/test_db.py`（表数 / 表清单守卫同步）
- `data/seed.py`（新用户初始余额 + 开户流水；**张三不动**）
- `data/dao.py`（device 读写原语 + 建用户/账户原语；逼近 300 行按既有方式拆）
- `tools/_query_common.py`（`_SESSION_USER` 改 **thread-local**）
- `agent/session.py`（**新文件**，放注册/登录薄函数；别塞 orchestrator）
- `interfaces/api/app.py`（加 `POST /api/session`）
- `interfaces/web/app.py`、`interfaces/web/components.py`（登录/注册闸门）
- `tests/`（新增单测 + 守卫）

接口：人类已批的契约变更，**逐字照此实现，不得自行改名/加字段**：
1. 新表 `device`（规格 §1 DDL 追加）：
   ```sql
   CREATE TABLE device (                             -- 卡 21：设备 → 用户绑定（识别，非认证）
     device_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, bound_at TEXT NOT NULL
   );
   ```
2. 新端点 **`POST /api/session`**
   - body：`{"device_id": "...", "phone": "13812345678"|null}`
   - 返回（**恰好 4 键**）：`{"status": "existing"|"registered"|"need_register", "user_id": "..."|null, "name": "..."|null, "masked_phone": "..."|null}`
   - `device_id` 未知 + 合法手机号 → 建 user + savings 账户（**余额 10,000,000 分 = 100,000.00 元**）+ **一笔初始入账 txn** + 绑 device → `status="registered"`
   - `device_id` 已知 → 返回其 user → `status="existing"`（**不需要**手机号）
   - `device_id` 未知且没给手机号 → `status="need_register"`（前端弹注册表单）
3. **不新增冻结工具**：§2 仍是 17 个。注册/登录属规格 §6 的**工具层私有约定**，不是工具契约。

要求：
1. **手机号必填（注册时）**，按铁律 8 脱敏为 `138****0001`；**完整手机号绝不落库、不回显、不进日志/审计**（与卡 20 同口径）。
2. **初始入账必须写一笔 `txn`**（`category` 用「开户」，`direction="in"`），否则破坏 `data/seed.py` 头注的「流水合计 = 余额变动」自检口径。
3. **张三（`u_zhangsan_0001`）保留既有历史与 46,634.00 余额，不重置**——它是账单图表/订阅等展示样本。
4. **多用户并发正确性（关键）**：`tools/_query_common.py` 的 `_SESSION_USER` 现在是**模块全局** → 两台设备同时用会互相覆盖，必须改 **thread-local**（照 `data` 层已做的线程局部连接先例）；`current_user_id()` 行为对旧调用不变（仍是"未设置 → demo 用户"）。
5. **越权拦截照旧**：`require_owned` 命中他 user 资源 → `FORBIDDEN`。
6. 网页端：未注册 → 显示「手机号注册」闸门；已注册 → 直接进聊天。`device_id` 由浏览器生成一次并存 `localStorage`，每次请求带上。
7. 文档（README / `docs/03-运行与评测.md`）写明：**设备 id 是「识别」不是「认证」**（客户端可伪造），注册**不做**短信验证码（demo 口径）。

禁止：改 `POST /api/chat` 的 6 个字段；改 17 个冻结工具契约；改已 PASS 卡的测试（`tests/test_db.py` 表数守卫除外）；新增依赖；界面直连 `tools/`/`data/`。

交付：按模板 + 截图三张（① 注册页 ② 注册后余额显示 100,000.00 元 ③ **同一个 `device_id` 再进** → 直接登录、看到同一余额）。
