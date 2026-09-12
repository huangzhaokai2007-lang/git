<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 11`（无人值守）或在 Hermes 里直接说「做卡 11」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #11】建立 tests/cases/*.yaml 用例集并接入 verify.sh
范围：tests/cases/、tests/test_cases.py、scripts/verify.sh
要求：
1. 按规格第 8 节的 YAML 格式实现用例驱动（至少 30 条）：
   账单 5 条 / 转账 6 条 / 订阅 5 条 / 卡片 4 条 / 理财 4 条 / 越权与安全 6 条
2. 每条断言 intent、tool_calls、tier、must_contain、must_not_contain、executed
3. test_cases.py 输出通过率，失败用例打印实际 vs 期望 diff
4. verify.sh 把用例测试纳入，失败即退出码非 0
交付：按模板，并贴出 30 条用例的真实通过率。
