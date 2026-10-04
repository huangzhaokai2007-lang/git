# 模拟银行卡绑定实现计划

**目标：**实现表单绑卡、服务端验证、独立绑定记录及已绑定卡查询；联动余额隐藏、详情与密码查看卡号。

**架构：** interfaces → agent → tools/guard → data。保留 T18 和冻结意图清单；新增独立表单入口及补充契约，使用标准库与现有依赖。现有 D 盘克隆使用 feature/f01-card-binding 分支。

## 范围与接口

- data/card_binding.py：增量创建 agent_card_secret、agent_card_binding、agent_card_request、agent_card_attempt；使用当前 DAO 连接与事务。
- data/card_binding_seed.py：只为现有三张合成卡补充完整演示卡号和逐卡密码哈希；已有数据不覆盖，不预先绑定。
- guard/card_credentials.py：PBKDF2 加盐哈希、常量时间校验、固定六位数字模拟密码。
- tools/card_binding.py：preview_binding(card_no, password, session_id)、confirm_binding(token, session_id)、list_bound_cards(status)、card_detail(card_id)、reveal_card_number(card_id, password, session_id)。
- agent/card_binding_flow.py：界面专用适配器，返回结构化显示结果；卡号与密码从不经过模型。
- interfaces/web/card_binding_ui.py：两步绑卡表单、已绑定列表、独立余额开关、详情与密码表单。
- interfaces/web/app.py：接入独立卡片页面和聊天卡查询后的列表。原 T18 不改变语义。

## 安全和状态规则

绑定按 L1 明确确认，预览只验证与生成五分钟令牌，确认才写绑定。令牌绑定用户、会话、卡片，重复确认幂等。连续五次凭证失败锁定该用户对该卡的验证五分钟；成功不绕过未到期锁定。卡片与账户均需属于当前用户。完整卡号查看每次重新验证密码，不写入模型、普通事实包、聊天记录或审计。

## 执行步骤

- [x] 写 tests/test_card_binding.py：无绑定、正确预览/确认、错误密码/卡号、跨用户/跨会话、确认过期、重复提交、并发、余额、完整卡号、锁定与审计；运行确认功能缺失的失败。
- [x] 实现数据、凭证护栏、工具和编排适配器；运行新测试及原 F01 回归。
- [x] 写 Streamlit AppTest：预览前无绑定、验证后明确确认、成功列表、余额开关、详情、卡号二次密码验证；运行确认界面缺失的失败，再实现界面。
- [x] 补 SPEC-CHANGE 文档、演示凭证与验收脚本；所有凭证均为合成演示数据。
- [x] 执行 uv run pytest -q、bash scripts/verify.sh，保存实际输出；检查差异、模块行数及函数长度。

## 验证命令

```powershell
uv run pytest tests/test_card_binding.py tests/test_card_binding_ui.py tests/test_tools_card_query.py tests/test_orchestrator_readonly.py
uv run pytest -q
```

Git Bash 执行 scripts/verify.sh；缓存、下载、临时库与测试临时目录均位于 D:\CodexProjects\F01-card-query。所有验收使用合成库与替身，不声明真实银行连接或真实模型识别通过。

## 离线卡片查询修复

- [x] 复现未配置 LLM_API_KEY 时“卡片查询”被判为 out_of_scope；先验证五个页面回归用例失败。
- [x] Web 入口完整匹配明确清单指令；保留注入检测、在途写操作优先级和原状态机，混合/写操作表达仍走分类器。
- [x] 新增九项离线/边界回归；verify.sh 全量 1126 passed，所有其他验收通过。
- [x] 重启本地演示保留绑定库；实际浏览器输入“卡片查询”显示 card_query、我的银行卡与绑定入口。
