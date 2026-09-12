<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 04`（无人值守）或在 Hermes 里直接说「做卡 04」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #04】实现 T1–T5（只读工具）
范围：tools/schemas.py、tools/query.py、tests/test_tools_query.py
要求：严格按规格第 2 节的入参名与 data 字段名实现
get_balance / list_txn / analyze_spending / detect_anomalies / generate_bill_report
1. 统一返回 ToolResult（含 facts 事实包：本结果允许被引用的所有数字）
2. analyze_spending 的 period 支持 "2026-09" 和 "2026"；分组支持 category 和 channel
3. detect_anomalies 用规则实现（金额 > 该用户近 90 天均值 3 倍、23:00-06:00 时段、同商户 1 小时内 ≥3 笔）
4. generate_bill_report 输出 Markdown，其中的每个数字都必须同时出现在 facts 里
单测：每个工具 ≥3 条，含 facts 与 markdown 中数字一致性的断言
禁止：把任何数字写死在代码里（除阈值常量并注明来源）
交付：按模板。
