# F11 · 理财推荐

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [五周排期](../04-功能排期.md)

**排期：W3。负责人、审核人、状态和 Issue/PR 统一登记在[认领表](README.md)，不在本页重复维护。**

## 要交付什么

用户示例：“推荐适合我的理财产品。”

现有基础（2026-09-22，`734ac09`）：已有读路由；测评衔接、风险/期限/金额筛选。

业务对应：`wealth_recommend` / `recommend_wealth`。

## 从哪里改

- 业务入口：[tools/_wealth_risk.py](../../tools/_wealth_risk.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_wealth_risk.py](../../tests/test_tools_wealth_risk.py)、[test_orchestrator_readonly.py](../../tests/test_orchestrator_readonly.py)
- 依赖：F10；当前传入 risk_level 也能推荐，需明确面向用户的测评来源。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 正常路径使用当前用户的有效测评结果并筛选期限、金额。
- [ ] 无匹配时明确告知，不放宽风险条件。
- [ ] 只推荐不申购，回执数字来自 facts。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_wealth_risk.py tests/test_orchestrator_readonly.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
