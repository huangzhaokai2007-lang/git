<!-- 由分析师代人类建立（人类已批契约变更 + 排期）；与 docs/02-AI指令剧本.md 的对应小节保持同步 -->

> 用法：`bash scripts/run-card.sh 24`（无人值守）或在 Hermes 里直接说「做卡 24」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。
> 本卡是 **W1 的重头戏**：它打出的「通用写路径」是后面 **9 个写功能**（W2 卡片 5 项、W3 理财 3 项、W4 AA、W5 送礼）的共同地基。

【任务卡 #24】接通「订阅取消」+ 泛化写路径（+ 规格计数机器守卫）

第一步：读 `CLAUDE.md`、`docs/01-接口规格.md`、`docs/cards/card-24.md`，用 5 行复述你理解的约束，等我确认再动手。

**背景（实测）**：`card_query` 那类「工具已实现、编排层没路由」的缺口共 13 类，card-23 修了 1 类。本卡修 `subscription_cancel`，**但重点不是它本身，而是把写路径从「转账专属」泛化成通用机制**——否则后面 9 个写功能每个都要重踩一遍。

**现状（读代码得到，非猜测）**：
- `agent/write_flow.py`（265 行）整条写路径是**转账专属**：`WRITE_INTENTS = ("transfer_single","transfer_scheduled")`、硬编码 `transfer.preview_transfer` / `transfer.execute_transfer`。
- **两种确认凭证并存**：
  - `preview_token` —— 由 `tools.transfer.preview_transfer` 签发（T7）
  - `confirm_ref` —— 由**工具层私有约定** `tools.subscription.issue_confirm_ref(action, target_id)` 签发（T11/T12/T15 用它），TTL 300s、绑定（action + target_id + 当前用户）
- `agent/orchestrator.py` 已 293 行（近顶格）；`tools/subscription.py` 已 **300 行顶格**；`data/dao.py` 已 **300 行顶格**。

范围：只允许改下列文件，其他一律不动。
- `agent/write_flow.py`（**泛化**：抽 per-intent 写描述符，支持两种凭证）
- `agent/write_intents.py`（**新文件**，放「意图 → 写描述符」表；避免 write_flow 撑破 300 行）
- `agent/orchestrator.py`（接 `subscription_cancel` 路由；已 293 行，允许照先例拆文件再导出）
- `agent/confirm_card.py`（确认卡要能承载 `confirm_ref` 型凭证）
- `agent/templates.py`（订阅取消的确认卡措辞 + 取消结果回执；**大白话**）
- `agent/classifier.py`（`subscription_cancel` 的槽位，若缺）
- `docs/01-接口规格.md`（**人类已批的 SPEC-CHANGE**：见下「接口」）
- `README.md` / `docs/00-施工包说明.md` / `docs/02-AI指令剧本.md` / `docs/cards/README.md`（连锁同步）
- `tests/`（新增单测 + **`tests/test_spec_counts.py` 机器守卫**）

接口：人类已批，**逐字照此**：
1. **不新增冻结工具**（§2 仍是 18 个）。`subscription_cancel` 用的是**既有 T11** `cancel_subscription(sub_id, confirm_ref)` —— 它本来就实现了 L2 + confirm_ref 闭环，本卡只是**给它接上编排层路由**。
2. **凭证抽象**（本卡新增的编排层内部概念，不进 §2）：每个写意图声明自己需要哪种凭证 —— `preview_token` 或 `confirm_ref`。**转账行为必须与现在完全一致**（既有测试是判据）。
3. **规格计数机器守卫**（人类已批）：新增 `tests/test_spec_counts.py`，读 `docs/01-接口规格.md` §2，断言：
   - 标题里的数字 **==** 表内 `| Tn |` 行数 **==** 序号从 1 连续到 N
   - 失败要**给出可读诊断**（如「标题写 18，表内只有 17 行，缺 T17」），不是只报 False

要求：
1. **取消订阅的完整链路**：用户说「把那个会员退了」→ 解析到订阅（重名要追问）→ **确认卡**（商户 / 金额 / 周期 / 权限档 L2 / 需要验证码）→ 用户确认 + OTP → 执行 → 回执（大白话）。
   - 确认卡的**每个数字都要能在 `facts` 找到**；OTP 校验与幂等**复用工具层**（`cancel_subscription` 内部已做，**不要重写**）。
2. **泛化不得改变转账行为**：`transfer_single` / `transfer_scheduled` 的既有用例（含 `tests/cases/orchestrator.yaml` 的 trf-* 与 card-20 的用例）**必须全绿**。这是本卡最大的风险点。
3. **`confirm_ref` 的签发时机**：在 PRECHECK→CONFIRM_CARD 之间（对标现在转账调 `preview_transfer` 的位置）；**必须在工具层签发**（`issue_confirm_ref`），编排层不得自己造串。
4. **归属与越权**：订阅必须属于当前用户，越权 → `FORBIDDEN`（工具层已实现，别绕过）。
5. **禁用行数**：`tools/subscription.py` / `data/dao.py` 已顶格 300，**一个字都不许加**；`agent/write_flow.py`、`agent/orchestrator.py` 逼近 300 → 该拆就拆（允许新增 `agent/write_intents.py`，或把读路由/写路由继续分离）。
6. **门禁**：不破 **1085 passed** 基线；`bash scripts/verify.sh` **6/6 全绿**；新增用例要有牙（变异抽查必须真变红）。
7. **不改已 PASS 卡的测试**——**除非**卡自身要求与它冲突（本卡要求 5 要求「订阅取消」必须走通；若为此必须改某条既有断言，**先在交付说明里列出来等我追认**，别自己默默改）。

禁止：改 `POST /api/chat` 的 6 个字段；改 §2 的 18 个冻结工具签名；新增依赖；界面直连 `tools/`/`data/`；重写 `cancel_subscription` 内部的 OTP/幂等/confirm_ref 逻辑。

交付：按模板 + 截图两张（① 说「把那个会员退了」→ 出 L2 确认卡 ② 确认 + 验证码后 → 取消成功回执）。
