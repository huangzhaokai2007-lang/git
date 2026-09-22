# F14 · 账单分析

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [五周排期](../04-功能排期.md)

**排期：W3。负责人、审核人、状态和 Issue/PR 统一登记在[认领表](README.md)，不在本页重复维护。**

## 要交付什么

用户示例：“上个月主要把钱花在哪里？”

现有基础（2026-09-22，`734ac09`）：已有读路由；分类、环比及时间口径。

业务对应：`bill_analysis` / `analyze_spending`。

## 从哪里改

- 业务入口：[tools/query.py](../../tools/query.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_query_analysis.py](../../tests/test_query_analysis.py)、[test_period_normalization.py](../../tests/test_period_normalization.py)
- 依赖：F02 + 时间归一化。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 分类汇总与流水支出对得上，占比和环比来自工具。
- [ ] 上期为零、当期无记录和跨年有测试。
- [ ] 无法识别的期间先追问，不猜日期。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_query_analysis.py tests/test_period_normalization.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
