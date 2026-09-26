# F12 · 理财申购

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [四周排期](../04-功能排期.md)

**本轮负责人、审核人、计划周次、状态和 Issue/PR 见[功能总表](README.md)。** 本页保留技术范围与验收点。

## 要交付什么

用户示例：“购买我选中的理财产品。”

现有基础（2026-09-22，`734ac09`）：工具已有、聊天未接；有效测评、起购额、余额与幂等。

业务对应：`wealth_buy` / `trade_wealth(buy)`。

## 从哪里改

- 业务入口：[tools/wealth.py](../../tools/wealth.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_wealth_trade.py](../../tests/test_tools_wealth_trade.py)、[test_tools_wealth_risk.py](../../tests/test_tools_wealth_risk.py)
- 依赖：F04 + F10；F11 可作为选品入口。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 缺失或过期测评、超风险、低于起购额、余额不足均拒绝。
- [ ] L2 确认加 OTP 后才交易，T15 的 OTP 闸门在编排层。
- [ ] 余额、流水与持仓一致，同一确认不重复扣款。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_wealth_trade.py tests/test_tools_wealth_risk.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
