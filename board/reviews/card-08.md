VERDICT: PASS
CARDS: card-08
REVIEWED_DIFF: 3 个未跟踪新文件 —— agent/llm.py(+99)、agent/classifier.py(+158)、tests/test_classifier.py(+291)。无越界改动（git status 仅这 3 个 ??）。
CHECKED:
  - 接口一致性：通过。IntentOut 字段（intent/confidence/slots/missing_slots/unsafe_reason）与规格 §3 逐字对齐，extra=forbid；25 个意图 label 与规格 §3 逐字一致（测试用独立字面量 SPEC_INTENTS 钉住，len==25）；intent 用 Literal 强约束不得发明新意图；unsafe_reason 仅在 unsafe_request 必填（model_validator）。
  - 越界改动：无。classifier 只做「理解」，静态断言（源码扫 grep）确认不含 import guard / permission_tier / requires_otp / audit_log / OVER_LIMIT / dao. 等任何权限/业务/数据层痕迹（铁律 8）；llm.py 只 import openai/dotenv/pydantic，import 阶段不构造客户端、不联网（子进程验证清 key 后 import 成功）。
  - 测试真实性（变异抽查 3/3 真报警，均已还原）：① classifier 重试 `for attempt in (1,2)` 改成 `(1,)` → test_classify_retries_once_then_succeeds + falls_back ×3 共 4 条 FAIL；② check_structure `if unknown:` 改成 `if False:` → test_unknown_slot_name_is_a_structural_failure + test_unknown_missing_slot_name 2 条 FAIL；③ llm.MAX_RETRIES 2→0 → test_chat_json_retries_twice_then_raises_unavailable ×3 + recovers_on 共 4 条 FAIL。还原后 675 passed 恢复。
  - 边界与异常：通过。超时 20s / 重试 2 次（总 3 次）/ 用尽抛 LLMUnavailable 且不返回假数据；JSON 非法 / 字段缺失 / 超时三类失败路径各有独立用例；缺 key 在联网前抛 LLMUnavailable（报错不含 key 本身，另有专门断言 key 不泄漏）；classify 校验失败重试一次→out_of_scope（0 置信度 + 空槽位，不抛异常不编内容）；槽位名越界（slots 或 missing_slots）→ ClassifierOutputError。
  - 权限/审计/安全：通过。铁律 7（用户原话只进 user 消息，system prompt 只有契约，测试断言用户文本不在 system 里）；铁律 6（import 不联网、不读真实 .env 值——autouse fixture 把 load_dotenv 变 no-op）；LLM 不判权限不碰业务数字（铁律 1）；零网络依赖（全部 monkeypatch 假 LLM）。
RISKS:
  1. SLOT_SCHEMA 是 worker 按 §2 工具签名自行归纳的（规格 §3 只给意图清单、没给「每意图允许的槽位表」）。这张表就是编排层的意图契约——若某意图的槽位名归纳错（例如 transfer_single 该用 payee_id 还是 payee、amount 是分还是元），下游槽位抽取会系统性错。已记「需要人类决定」，但建议在进 card-09 前由人类拍板这张表（尤其 transfer/wealth 类金额槽位的单位口径）。
  2. 未接真实模型联调（卡 08 第 4 条要求假 LLM，属规格内）。真实 DeepSeek 在 temperature=0.0 + response_format=json_object 下的实际返回（是否稳定产出合法 JSON、超时率、重试能否兜住）未验证。风险：真实故障率若显著高于预期，2 次重试可能不够，但当前兜底（out_of_scope）保证不崩。建议 card-09 联调时观察真实失败率，再决定是否上调重试次数。
  3. classify 的 `except ValidationError` 属防御性分支：生产里 llm.chat_json 已把 ValidationError 收敛成 LLMUnavailable 再抛出，故该分支实际不可达（仅测试里 monkeypatch chat_json 直接抛 ValidationError 时才走到）。非 bug，纯防御。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 675 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 675 passed、3/5–5/5 SKIP、`全部通过 ✅`
  - `git status --short` → 仅 ?? agent/llm.py、agent/classifier.py、tests/test_classifier.py（范围干净）
  - 变异①（classifier 重试删去）→ 4 FAIL（已还原）；变异②（槽位校验置空）→ 2 FAIL（已还原）；变异③（MAX_RETRIES→0）→ 4 FAIL（已还原）
  - 还原后复跑 675 passed；grep 确认 MAX_RETRIES=2、`if unknown:`、`for attempt in (1,2):` 均已恢复
VERDICT_REASON: 3 文件范围干净、675 测试稳定全绿、JSON 二次校验/超时重试/降级兜底/槽位名校验/铁律 7 全部落实且变异抽查证伪有效，无 MUST_FIX；仅 SLOT_SCHEMA 口径与真实模型联调两条非阻塞 RISK。
