VERDICT: PASS
CARDS: card-12b
REVIEWED_DIFF: 6 个文件（已提交 b6eee07，本审为提交后复核）—— agent/orchestrator.py（detect 接线）、agent/templates.py（sanitize_facts + polish 收口）、guard/injection.py（规则层 + wrap_untrusted，card-12 已有，本卡接线）、tests/test_injection.py、tests/test_injection_wiring.py、scripts/verify.sh（第 5 段 SKIP→通过）。
CHECKED:
  - 接口一致性：通过。detect() 返回 Verdict{blocked,rule_ids,reason,intent}，命中 intent=unsafe_request 与规格 §3 一致；wrap_untrusted 输出 `<untrusted_data source="...">…</untrusted_data>`，source 只保留字母数字点划线下划线、正文 `<`/`>` 全角化（防提前闭合标签）；FREE_TEXT_FIELDS 清单（memo/note/remark/counterparty/message/body/content/text）可溯源。
  - 越界改动：无。detect 接线只在 orchestrator.handle 的 CLASSIFY 前加 `injection.detect(text)`，命中 → REFUSE；templates.polish 把 `sanitize_facts(facts)` 送进 LLM 载荷。
  - 测试真实性（变异抽查 2/2 真报警，均已还原）：① orchestrator 的 `if screen.blocked:` 改成 `if False` → test_injection_is_refused_before_any_llm_call 1 条 FAIL（报「注入请求不得触达 LLM」，证明零 LLM 调用是真的）；② templates.sanitize_facts 的包裹条件加 `False` → test_polish_wraps_free_text_and_keeps_numbers + test_sanitize_facts_recurses_into_nested_items 2 条 FAIL。还原后 805 恢复。
  - 边界与异常：通过。规则层归一化（解 unicode/十六进制/百分号转义 + NFKC + 去零宽 + 紧凑串）使「忽 略 之 前」分段混淆能命中；inj-transfer-all 收紧后「把余额全部转给李四/给王五转 100 元/上个月花了多少」零误报（test_benign_balance_phrasing_is_no_longer_a_false_positive），canonical 攻击仍命中（test_canonical_attack_is_still_blocked）；基线不降（805 = 799 + 6 新增，34 攻击串 100% 拦截、14 良性 0 误报）。
  - 权限/审计/安全：通过。注入命中 → REFUSE 且零 LLM 调用（spy 断言 chat_json 未被调用、CLASSIFY 未进状态）；铁律 7 收口（polish 送 LLM 的自由文本字段先 wrap_untrusted，嵌套 items 递归包裹，数字原样保留不影响 verify_numbers）；lint（AST 扫 agent/ 下 chat_json 调用点，自由文本未过包裹层即红）。
RISKS:
  1. memo/IM 正文的注入指令不被 detect 拦：detect() 的 `extra_texts` 参数已备但无调用点（orchestrator 只对用户主文本 detect，不查 memo/备注/IM 正文）。铁律 7 的「自由文本当数据」在 polish 已收口，但「自由文本当注入源」的检测要单开卡。攻击者若把注入藏在备注里，当前不拦（但会进 sanitize_facts 包裹，进 LLM 时已当数据）。
  2. sanitize_facts 的 FREE_TEXT_FIELDS 白名单是手动点维护：未来新增自由文本字段（如收款人备注、礼物备注）若忘加进清单，会漏包裹。建议后续把「自由文本字段」集中声明或走 schema 标注。
  3. 真实 DeepSeek 对 `<untrusted_data>` 标签的响应未验证（待卡 14 冒烟）：标签是否真能让模型把正文当数据、不执行其中指令，属黑盒未测。
  4. `_is_wrapped` 靠认 wrap_untrusted/sanitize_facts 两个函数名做 AST lint，若重构改名会静默失效（lint 有 `sites >= 2` 自检兜底，但改名后需同步）。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 805 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 805 passed、第 5 段「58 passed」（SKIP→通过）、`全部通过 ✅`
  - `git status --short` → 干净（本卡 6 文件已提交 b6eee07，无漂移）
  - 变异①（detect 拦截改 if False）→ test_injection_is_refused_before_any_llm_call 1 FAIL（已还原）；变异②（sanitize_facts 包裹改 if False）→ 2 FAIL（已还原）
  - 还原后复跑 805 passed；grep 确认无 `False and screen.blocked` / `False and key in` 残留
VERDICT_REASON: detect 接线在 CLASSIFY 前、命中零 LLM 调用且变异实测真报警，铁律 7 sanitize_facts 包裹自由文本且数字不受影响，基线不降（34/34 拦截 0/14 误报、805 无回归），无 MUST_FIX；仅 memo 注入检测未接线与白名单点维护等 4 条非阻塞 RISK。
