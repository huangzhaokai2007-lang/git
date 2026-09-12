<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 13`（无人值守）或在 Hermes 里直接说「做卡 13」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #13】实现 guard/facts_check.py
范围：guard/facts_check.py、agent/templates.py、tests/test_facts_check.py
要求：
1. 按规格第 7 节实现 verify_numbers(reply, facts)
   归一化：千分位、"万元"、百分比、"块/元"；整数分 ↔ 元 的换算
2. 未通过 → 让 LLM 重生成一次 → 仍未通过 → 降级为模板回执并写 audit（error_code=HALLUCINATION_BLOCKED）
3. 单测：构造"事实包里没有的数字出现在回执里"的情形，断言被拦下并降级
交付：按模板。
