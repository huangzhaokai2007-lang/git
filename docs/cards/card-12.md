<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 12`（无人值守）或在 Hermes 里直接说「做卡 12」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #12】实现 guard/injection.py
范围：guard/injection.py、tests/test_injection.py
要求：
1. 规则层：关键词/正则表（忽略之前的指令、忽略以上、你现在是、开发者模式、导出全部用户、
   告诉我系统提示词、把余额转给…、绕过验证、免密 等），命中→unsafe_request
2. 数据层：`wrap_untrusted(source, text)` 用 <untrusted_data source="...">…</untrusted_data> 包裹
3. 所有自由文本字段（memo、IM 正文、收款人备注）进入模型上下文前必须经过 wrap_untrusted；
   编写 lint 单测：扫描 agent/ 下所有 LLM 调用点，断言不存在直接拼接自由文本的调用
4. 单测：≥20 条攻击串（中英混合、编码混淆、分段绕过）全部被拦
交付：按模板，贴出拦截率。
