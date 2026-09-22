# F22 · 新增收款人

[全部功能 / 认领与进度](README.md) · [协作规则](../../CONTRIBUTING.md) · [五周排期](../04-功能排期.md)

**排期：W5。负责人、审核人、状态和 Issue/PR 统一登记在[认领表](README.md)，不在本页重复维护。**

## 要交付什么

用户示例：“添加一个新的收款人。”

现有基础（2026-09-22，`734ac09`）：聊天引导至表单/API；脱敏、重复及新增后转账。

业务对应：`payee_add` / `add_payee`。

添加联系人本身不动资金：当前 L1 流程以表单提交作为用户确认，不需要 OTP；新增后的转账仍按转账风险档位要求确认与验证码。

## 从哪里改

- 业务入口：[agent/payee_flow.py](../../agent/payee_flow.py)、[tools/payee.py](../../tools/payee.py)
- 编排入口：[收款人表单流程](../../agent/payee_flow.py)；读路由见 [read_routes.py](../../agent/read_routes.py)，转账写流程见 [write_flow.py](../../agent/write_flow.py)。后续泛化文件以 F04 合并结果为准。
- 现有回归：[test_tools_payee.py](../../tests/test_tools_payee.py)、[test_payee_flow.py](../../tests/test_payee_flow.py)、[test_api_payee.py](../../tests/test_api_payee.py)、[test_web_payee_form.py](../../tests/test_web_payee_form.py)
- 依赖：现有表单/API；供 F17/F21 使用。

这些是定位入口，不意味着要改全部文件。实现仍落在原有五层目录；共享文件先与本周集成人员约定，新增契约或依赖先按项目规则审批。

## 本轮验收清单

- [ ] 聊天引导表单，API 与表单复用 agent/payee_flow.py。
- [ ] 手机号脱敏后入库，完整号不回显或进入审计。
- [ ] 非法输入、重复联系人与新增后转账有验证。
- [ ] 演示正常、边界与失败路径，保留截图或可复现命令；用户可见数字来自工具事实包。
- [ ] 新增/调整行为有对应测试，相关原有回归通过；提供完整验收输出，由另一名组员审核。

现有测试起点（不代替新增场景与全量验收）：

```bash
uv run pytest tests/test_tools_payee.py tests/test_payee_flow.py tests/test_api_payee.py tests/test_web_payee_form.py
```

交付时在 PR 说明“改了什么 / 实际验证 / 剩余限制”，使用仓库 PR 模板。不能仅凭工具单测通过把对话流程标为已验收。
