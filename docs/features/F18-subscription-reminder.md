# F18 · 订阅提醒

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [四周排期](../04-功能排期.md)

**本轮负责人、审核人、计划周次、状态和 Issue/PR 见[功能总表](README.md)。** 本页保留技术范围与验收点。

## 要交付什么

用户示例：“在会员下次扣费前提醒我。”

现有基础（2026-09-22，`734ac09`）：先明确触发时点、展示渠道、去重与工具契约，再实现。

业务对应：`subscription_remind` / T53 `list_due_subscriptions`（**L0 只读，已实现**）。

## 实现口径（已落地）

开工前要求的四件事，逐条定论如下（代码位置一并给出，便于复核）：

| 问题 | 定论 | 落在哪 |
|---|---|---|
| 提前多久 | 默认 **7 天**，可传 1–30；太早记不住、太晚来不及取消 | `tools/subscription_remind.DEFAULT_WITHIN_DAYS` |
| 主动推送还是查询展示 | **查询展示**。没有 cron/MQ/长连接，不做真推送；界面用**常驻提醒条**（只读、不去重），任何操作后都能看到 | `agent/subscription_notice.summary()` + `interfaces/web/subscription_ui.banner()` |
| 是否持久化 | **不持久化**：不建表、不存已提醒状态 | — |
| 取消方式 | 复用 T11 `subscription_cancel`（L2 确认卡），提醒条与列表行内都给入口 | `interfaces/web/subscription_ui.render_turn()` |

两条补充口径（实现中发现的问题，已单独处理）：

- **逾期单列**：`status='active'` 但扣费日早于 `AS_OF` 的订阅不进 `items`（那不是"将要扣费"），
  而是进 facts 的 `overdue_items` 并单独提示 —— 它意味着「该扣的没扣成」，静默丢掉会让用户
  永远不知道。列表页也会给这类订阅打「已过 N 天」标记。
- **不做「恢复订阅」**：`cancelled` 是终态。真实世界里取消是在商户侧终止协议，银行只能记录，
  重新订阅属于重新签约；因此不提供"撤销取消"，也不在已取消列表里给按钮。

## 从哪里改

- 业务入口：`tools/subscription_remind.py`（T53）；订阅列表与取消在 `tools/subscription.py`（T10/T11）。
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_subscription.py](../../tests/test_tools_subscription.py)
- 依赖：先确定提醒形态与契约；F19 订阅列表提供事实。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

这里列出的测试是可复用的订阅基础测试，不是提醒功能已经存在的证明；提醒实现需新增对应测试。

## 本轮验收清单

- [ ] 开工前写清提前多久、主动推送还是查询展示、是否持久化及取消方式。
- [ ] 实现后覆盖日期边界、重复提醒、取消订阅和用户隔离。
- [ ] 新增工具、字段或依赖先走规格审批，禁止伪装成已具备提醒能力。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_subscription.py
```

本功能的测试（新增场景，供复核时定位）：

```bash
uv run pytest tests/test_subscription_remind.py tests/test_subscription_notice.py \
              tests/test_subscription_ui.py tests/test_subscription_overdue.py
```

- `test_subscription_remind.py`：日期边界（闭区间两端）、过期不算、只查 active、用户隔离、参数校验、幂等
- `test_subscription_notice.py`：概览只读可重复、侧边栏会话不触发、取数失败不抛
- `test_subscription_ui.py`：每行都有「取消订阅」、已取消列表不给按钮、提醒条、端到端「管订阅 → 取消 → 确认卡」
- `test_subscription_overdue.py`：逾期单独报且不进 `items`、两层渲染逐字同源、列表页标记

可复现的演示路径（重置数据后）：

```bash
uv run python -m data.seed --reset      # 云音乐回到 active、下次扣费 2026-09-18
uv run streamlit run interfaces/web/app.py
# 首屏即见「订阅提醒 · 1 个订阅将在 7 天内扣费（合计 15.00 元）」
# 点「管订阅」→ 每行有「取消订阅」；点它会出确认卡，未确认不会真的取消
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
