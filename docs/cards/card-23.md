<!-- 由分析师代人类建立（人类已批契约变更）；与 docs/02-AI指令剧本.md 的对应小节保持同步 -->

> 用法：`bash scripts/run-card.sh 23`（无人值守）或在 Hermes 里直接说「做卡 23」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #23】接通「卡片查询」：新增只读工具 T18 `list_cards` + 编排层路由

第一步：读 `CLAUDE.md` 和 `docs/01-接口规格.md`，用 5 行复述你理解的约束，等我确认再动手。

**背景（实测）**：把规格 §3 的意图逐条喂给编排层，发现 `card_query` 回「这个功能还没接通」——因为 §2 里**没有任何只读的读卡工具**（T12 `manage_card` 的 action 全是写：apply/adjust_limit/set_txn_limit/lock/unlock/report_lost）。**库里有 3 张卡却没有工具能读**。本卡补上这块。

范围：只允许改下列文件，其他一律不动。
- `docs/01-接口规格.md`（**人类已批的 SPEC-CHANGE**：见下「接口」三条）
- `docs/00-施工包说明.md`（「17 个工具契约」计数 → 18）
- `docs/02-AI指令剧本.md`（仅同步卡 21/22 文本里的「17 个」→18，外加本卡自身小节）
- `docs/cards/README.md`（卡索引加 23）
- `README.md`（§5 工具表加 T18 行 + 相关计数）
- `tools/card_query.py`（**新文件**，放 `list_cards`；**不要**塞进 `tools/card.py`——它已 272 行，加进去必破 300）
- `tools/schemas.py`（加 `ListCardsReq` / `ListCardsData`）
- `data/_dao_core.py`（加 `list_cards(user_id, status=None)` DAO 原语）—— **`data/dao.py` 已顶格 300 行，一个字都不许再加**
- `agent/orchestrator.py`（把 `card_query → list_cards` 接进 `TOOL_ROUTES`；它已 **293 行**，允许按 `agent/payee_flow.py` 的先例拆出 `agent/read_routes.py` 再导出）
- `tests/`（新增单测 + 路由测试）

接口：人类已批的契约变更，**逐字照此实现，不得自行改名/加字段**：
1. 新工具 **T18 `list_cards(status=None) -> ToolResult`**
   - 入参 `status` 可选：`normal` / `locked` / `lost`；**不传 = 全部**
   - `data` = `{"items": [{"card_id", "card_no_mask", "type", "status", "credit_limit", "single_limit", "daily_limit"}], "total_count": int}`
   - **金额一律整数分**；储蓄卡没有授信额度 → `credit_limit` 为 **`null`**（不是 0、不是编一个数）
   - 权限档 **L0**（只读）
   - 归属：只读**当前 user**（`current_user_id()` 口径）；他人的卡一张都不能出现
2. §2 计数 **17 → 18**：标题 `## 2. 工具函数契约（18 个，名字与字段名冻结）` + 表加 T18 行
3. §6 那句「不属于 17 个冻结工具契约」→ **18**（注册/登录等私有约定仍不属于冻结工具）

要求：
1. **绝不回显完整卡号**：库里只有 `card_no_mask`（形如 `6222 **** **** 0001`），照抄即可；**不得拼接、补全、猜测**
2. 数字只来自库（铁律 2）；`facts` 里放回执会用到的数字（`total_count` + 各卡额度），回执里的每个数字都能在 `facts` 找到
3. `status` 给了但认不出 → `INVALID_ARGUMENT`（**不得静默忽略**，否则用户以为过滤了）
4. **库里现在有 3 张卡（张三）**：`card_savings_0001` normal / `card_credit_0002` credit normal（额度 30,000.00 元）/ `card_savings_0003` savings **lost**。单测与截图**都要覆盖到"已挂失"那张**（这是有信息量的一条）
5. **路由**：`card_query` 意图 → `list_cards`。聊天说「我几张卡」「我的卡」「我有没有挂失的卡」都要命中；**别改其他已接通意图的行为**
6. 「17 个工具」的连锁计数全仓同步。**`CLAUDE.md` 与 `.hermes.md` 是受保护文件——不要改，由 analyst 另行落地**
7. 不破 **1051 passed** 基线；`bash scripts/verify.sh` 6/6 全绿

禁止：改 `POST /api/chat` 的 6 个字段；改任何已 PASS 卡的测试；新增依赖；界面直连 `tools/`/`data/`；把 `tools/card.py` 或 `data/dao.py` 撑破 300 行。

交付：按模板 + 截图两张（① 聊天问「我几张卡」→ 列出 3 张，含已挂失那张 ② 问「我有没有挂失的卡」→ 只回该状态）。
