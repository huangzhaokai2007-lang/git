<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 14`（无人值守）或在 Hermes 里直接说「做卡 14」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #14】工具层安全加固
范围：guard/tool_guard.py、tools/*.py、tests/test_tool_guard.py
要求：
1. 所有工具入口统一经过 tool_guard：校验资源 id 属于当前 user（否则 FORBIDDEN 且写 risk_event）
2. 限流：同一用户 60 秒内写操作 > 5 次 → 拒绝并提示
3. 幂等表落库（不是内存），进程重启后仍有效
4. 参数边界：金额必须为正整数分、上限 500 万分、收款人 id 存在
5. 单测：越权访问他人账户/卡/持仓；重放同一 token；非法金额
交付：按模板。
