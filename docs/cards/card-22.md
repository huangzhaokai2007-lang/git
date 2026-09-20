<!-- 由分析师代人类建立（人类已批契约变更）；与 docs/02-AI指令剧本.md 的对应小节保持同步 -->

> 用法：`bash scripts/run-card.sh 22`（无人值守）或在 Hermes 里直接说「做卡 22」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。**依赖卡 21 先落地**（需要用户身份）。

【任务卡 #22】每用户聊天记录持久化（落库 → 按用户加载 → 安全回放）

第一步：读 `CLAUDE.md` 和 `docs/01-接口规格.md`，用 5 行复述你理解的约束，等我确认再动手。

范围：只允许改下列文件，其他一律不动。
- `docs/01-接口规格.md`（**人类已批的 SPEC-CHANGE**：见下「接口」第 1 条）
- `data/schema.sql`（加 `conversation` 表）
- `tests/test_db.py`（表数 / 表清单守卫同步）
- `data/dao.py`（conversation 读写原语）
- `agent/orchestrator.py`（每个 turn 落库；首轮按当前用户加载历史；若逼近 300 行按既有方式拆）
- `interfaces/api/app.py`（加 `POST /api/history`）
- `interfaces/web/app.py`、`interfaces/web/components.py`（登录后加载并渲染该用户历史）
- `tests/`（新增单测 + 守卫）

接口：人类已批的契约变更，**逐字照此实现**：
1. 新表 `conversation`（规格 §1 DDL 追加）：
   ```sql
   CREATE TABLE conversation (                       -- 卡 22：每用户聊天记录（原文，回放时包裹）
     id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
     role TEXT NOT NULL,                             -- user | assistant
     content TEXT NOT NULL, intent TEXT, trace_id TEXT,
     created_at TEXT NOT NULL                        -- ISO8601
   );
   ```
2. 每个 turn 落库**两行**（user 原文 + assistant 回执）；按 `user_id` + `created_at` 升序读回。
3. 新端点 **`POST /api/history`**：body `{"device_id": "...", "limit": 50}` →
   用 `device_id` 解析出 user（同卡 21 口径）再返回**该用户**的历史；**不接受任意 `user_id` 入参**（否则就是越权读取）。
   返回形状与既有响应**分开**（历史列表，不是那 6 个字段），字段名在实现时定稿并写进 §2/§3 附近的口径说明。

要求：
1. **铁律 7（关键）**：**存原文，回放时包裹**——历史里的用户自由文本进模型上下文前**必须**过 `wrap_untrusted()`（照 `agent/channel.py` 已有的 seam），**不得裸拼**进 prompt。
2. **铁律 2**：回执数字仍只来自事实包；**历史回放只做上下文，不得当作事实来源**（不许模型从历史里"读出"一个数字来回答）。
3. 只读写**当前用户**（`set_current_user` 口径）；跨 user 读历史 → `FORBIDDEN`。
4. **脱敏**：会话里若出现完整手机号（注册场景），按既有口径处理，**完整号不进 `conversation`**。
5. 网页端：登录后**自动加载**该用户历史并渲染；**换一个 device_id 看不到上一个用户的历史**。
6. 单文件 ≤300 行、单函数 ≤40 行照旧。

禁止：改 `POST /api/chat` 的 6 个字段；改 17 个冻结工具；改已 PASS 卡的测试（`tests/test_db.py` 守卫除外）；新增依赖；界面直连 `tools/`/`data/`。

交付：按模板 + 截图两张（① 聊几句 → 刷新/重登后历史仍在 ② 换 `device_id` → 看不到上一个用户的历史）。
