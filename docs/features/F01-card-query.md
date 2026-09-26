# F01 · 卡片查询

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [四周排期](../04-功能排期.md)

**本轮负责人、审核人、计划周次、状态和 Issue/PR 见[功能总表](README.md)。** 本页保留技术范围与验收点。

## 要交付什么

用户示例：“我有哪些卡？只看挂失的。”

现有基础（2026-09-22，`734ac09`）：已有读路由；精细化状态筛选、空结果和卡号脱敏。

业务对应：`card_query` / `list_cards`。

## 从哪里改

- 业务入口：[tools/card_query.py](../../tools/card_query.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_card_query.py](../../tests/test_tools_card_query.py)、[test_orchestrator_readonly.py](../../tests/test_orchestrator_readonly.py)
- 依赖：无；为 F05–F09 提供选卡入口。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 只返回当前用户的卡，卡号沿用脱敏值。
- [ ] 按状态筛选后的 total_count 与条数一致。
- [ ] 无卡返回清楚的空状态，非法状态不能静默忽略。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_card_query.py tests/test_orchestrator_readonly.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
