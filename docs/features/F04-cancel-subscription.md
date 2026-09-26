# F04 · 订阅取消

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [四周排期](../04-功能排期.md)

**本轮负责人、审核人、计划周次、状态和 Issue/PR 见[功能总表](README.md)。** 本页保留技术范围与验收点。

## 要交付什么

用户示例：“取消我的视频会员订阅。”

现有基础（2026-09-22，`734ac09`）：工具已有、聊天未接；先建设通用确认与写入流程。

业务对应：`subscription_cancel` / `cancel_subscription`。

## 从哪里改

- 业务入口：[tools/subscription.py](../../tools/subscription.py)
- 编排入口：[状态机](../../agent/orchestrator.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_subscription_cancel.py](../../tests/test_tools_subscription_cancel.py)、[test_confirm_flow.py](../../tests/test_confirm_flow.py)、[test_cases.py](../../tests/test_cases.py)
- 依赖：公共写流程先行；既有施工卡 card-24。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

参考既有 [card-24](../cards/card-24.md)，但以[最新台账口径](../../board/ledger.md)为准：T11/T15 的 OTP 在编排层，T8/T12 在工具层；merchant 槽位扩展已记录，不能照搬旧卡“OTP 全由工具处理”的描述。

## 本轮验收清单

- [ ] 唯一商户匹配才补 sub_id，重名追问、无匹配报错。
- [ ] L2 确认和 OTP 通过前不取消，T11 的 OTP 闸门在编排层。
- [ ] 凭证过期、跨用户和重复提交有测试，已有转账行为不变。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_subscription_cancel.py tests/test_confirm_flow.py tests/test_cases.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
