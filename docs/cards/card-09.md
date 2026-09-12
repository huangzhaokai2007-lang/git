<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 09`（无人值守）或在 Hermes 里直接说「做卡 09」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #09】实现 agent/orchestrator.py 状态机，先只支持 L0 只读意图
范围：agent/orchestrator.py、agent/templates.py、tests/test_orchestrator_readonly.py
要求：
1. 严格按规格第 4 节实现状态：IDLE→CLASSIFY→SLOT_FILL→PRECHECK→EXECUTE→VERIFY_NUMBERS→REPLY→AUDIT
2. 本卡只接通 L0 意图：balance_query / txn_query / bill_analysis / anomaly_check / bill_report /
   subscription_list / card_query / wealth_recommend
3. 置信度 <0.6 走 CLARIFY 追问（最多 2 轮）；缺槽同理
4. 每个请求生成 trace_id，全程写 audit_log
5. 回执优先用 templates.py 的模板，LLM 只允许做措辞润色且不得改动任何数字
6. 单测用假 LLM：10 条典型输入 → 断言 tool_calls 与 templates 输出
禁止：本卡不要碰任何写操作
交付：按模板。
