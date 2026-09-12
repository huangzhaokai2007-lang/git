<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 08`（无人值守）或在 Hermes 里直接说「做卡 08」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #08】实现 agent/llm.py 与 agent/classifier.py
范围：agent/llm.py、agent/classifier.py、tests/test_classifier.py
要求：
1. llm.py：用 openai SDK 调 OpenAI 兼容接口（base_url/key/model 从 .env 读）；
   提供 `chat_json(system, user, schema)`，用 response_format=json_object + Pydantic 二次校验；
   超时 20s，失败重试 2 次，仍失败抛 LLMUnavailable
2. classifier.py：按规格第 3 节的意图清单与 IntentOut 模型实现意图识别 + 槽位抽取
3. 输出必须通过 Pydantic 校验；校验失败重试一次，再失败返回 intent=out_of_scope
4. 单测用**假的 LLM**（monkeypatch），不依赖真实网络：覆盖正常、JSON 非法、超时、字段缺失
禁止：在 classifier 里做任何权限判断或业务判断
交付：按模板。
