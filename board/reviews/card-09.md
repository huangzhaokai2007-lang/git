VERDICT: PASS
CARDS: card-09
REVIEWED_DIFF: 3 个未跟踪新文件 —— agent/orchestrator.py(+253)、agent/templates.py(+239)、tests/test_orchestrator_readonly.py(+220)。无越界改动（git status 仅这 3 个 ??）。
CHECKED:
  - 接口一致性：通过。状态名（IDLE/CLASSIFY/CLARIFY/SLOT_FILL/PRECHECK/CONFIRM_CARD/PENDING_REVIEW/EXECUTE/VERIFY_NUMBERS/REPLY/REFUSE/AUDIT）与规格 §4 逐字一致；8 个只读意图（balance_query/txn_query/bill_analysis/anomaly_check/bill_report/subscription_list/card_query/wealth_recommend）与卡 09 第 2 条逐字一致；TOOL_ROUTES 只路由 7 个（card_query 无对应读卡工具，留空走「未接通」）。
  - 越界改动：无写操作越界。静态红线（test_no_write_intent_has_a_route + 源码 grep）确认 orchestrator.py 不含 execute_transfer/cancel_subscription/manage_card/trade_wealth/plan_gift/create_aa_request/assess_risk 等任何写工具名；写意图与非路由意图一律「未接通」模板，tool_calls==[]。**但存在 data 层直调（见 RISK #1）**。
  - 测试真实性（变异抽查 2/2 真报警，均已还原）：① templates.verify_numbers 改成恒 `set()` → test_hallucinated_polish_is_rejected_then_degraded_to_template 1 条 FAIL（幻觉红线是真的）；② orchestrator._write_audit 置空 → test_every_request_writes_exactly_one_audit_row 1 条 FAIL（铁律 5 审计是真的）。还原后 697 passed 恢复。
  - 边界与异常：通过。状态机主线不可跳步（HAPPY_STATES 逐态断言）；置信度<0.6 → CLARIFY ≤2 轮（超出转人工 to_human=True）；缺槽（txn_query 显式区间代码不猜）→ CLARIFY 追问；unsafe_request → REFUSE 模板；工具失败（未测评推荐 → INVALID_STATE）走错误模板不编数字；润色幻觉 → 重生成一次 → 仍不过降级模板 + HALLUCINATION_BLOCKED；模板缺占位符炸（TemplateError 不静默留白）、模板无硬编码业务数字（grep 钉住）。
  - 权限/审计/安全：通过。每请求一条 audit_log 带 trace_id + L0 档（铁律 5）；数字全部从 facts 注入（铁律 1/2，VERIFY_NUMBERS 判据）；铁律 7（用户原话只进 user 消息，system prompt 只含契约）；LLM 只润色措辞不得改数字；REFUSE/CLARIFY 审计 result=rejected。
RISKS:
  1. 【重点，须在卡 13 或 09b 收敛】agent 直调 data 层（越层）：orchestrator.py `from data import dao` + `_write_audit` 里 `dao.insert_audit(...)` 写审计，以及 `from data.seed import AS_OF` 读时间锚点。违反 CLAUDE.md 架构「agent 只许调 guard/tools」。判定为 RISK 而非 MUST_FIX 的理由：这是「函数调用 + 常量导入」级越层（非 .hermes.md 明令禁止的「直接写 SQL」）；当前 guard/ 未实现（卡 12/13）、无 in-scope 替代（改 tools/_query_common 加审计 helper 超本卡范围）；insert_audit 已参数化无注入、AS_OF 是常量无安全影响。但这是真越层，建议 09b 把审计写改走 tools/_query_common 的 write_audit 包装 + AS_OF 走 tools 层访问器，或卡 13 走 guard 层。
  2. verify_numbers 是「最小可判版本」：归一化剥小数点/千分位（12,345.6 与 123456 同串），挡不住量级/小数点错位。当前模板由 facts 机械注入、无硬编码，残余风险低；卡 13 换 guard 数字校验器时应收紧（延续 card-04 RISK）。
  3. SLOT_FILL 复用 classifier slots + 代码补齐（少一次 LLM 往返）：合理（铁律 1 代码说了算），但若 classifier 槽位抽错（如把 payee 抽成 payee_id），代码补齐无法纠错。槽位表口径（card-08 RISK）须在进卡 10 写操作前由人类拍板。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 697 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 697 passed、3/5–5/5 SKIP、`全部通过 ✅`
  - `git status --short` → 仅 ?? agent/orchestrator.py、agent/templates.py、tests/test_orchestrator_readonly.py（范围干净）
  - 变异①（verify_numbers→恒 set()）→ test_hallucinated_polish_is_rejected_then_degraded_to_template 1 FAIL（已还原）；变异②（_write_audit 置空）→ test_every_request_writes_exactly_one_audit_row 1 FAIL（已还原）
  - 还原后复跑 697 passed；grep 确认无 `return set()` / `return  # noqa` 残留
VERDICT_REASON: 3 文件范围干净、697 测试稳定全绿、状态机严格按 §4 不可跳步、禁写操作/数字来自 facts/每请求审计/CLARIFY 追问全部落实且变异抽查证伪有效，无 MUST_FIX；唯一实质顾虑是 agent 直调 data 层的越层（判定 RISK 不阻塞，但须卡 13/09b 收敛）。
