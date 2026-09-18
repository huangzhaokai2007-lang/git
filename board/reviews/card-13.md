VERDICT: PASS
CARDS: card-13
REVIEWED_DIFF: 3 个文件（已提交 f100002，本审为提交后复核）—— guard/facts_check.py（+130，§7 数字校验器唯一实现）、agent/templates.py（verify_numbers 改为转发 facts_check，删掉 _digits/facts_digits 最小版）、tests/test_facts_check.py（+157，26 用例）。
CHECKED:
  - 接口一致性：通过。facts_check.verify_numbers 的签名是规格 §7 冻结的 `-> (bool, list[str])`；templates.verify_numbers 保留「未通过数字集合」形态做适配（`set() if passed else set(offenders)`），两套语义一致（test_signature_and_error_code_contract 钉住）；ERROR_CODE=HALLUCINATION_BLOCKED 与编排层 audit 实际写入一致（源码 grep 兜底）。
  - 越界改动：无。最小版判据 `_digits`/`facts_digits` 已删（test_no_dead_helpers_left_in_templates 钉住，不留两套口径）。
  - 测试真实性（变异抽查 3/3 真报警，均已还原）：① verify_numbers 改恒 `(True, [])` → test_fabricated_numbers_are_blocked ×5 + test_hallucinated_reply 共 6 条 FAIL（拦截是真的）；② `_forms` 万元分支改 `*Decimal(1)` → test_normalization_passes[万元] 1 条 FAIL（万元换算是真的）；③ templates.verify_numbers 改恒 `set()` → test_signature_and_error_code_contract 1 条 FAIL（模板层转发失效是真的）。还原后 831 恢复。
  - 边界与异常：通过。归一化覆盖千分位（46,634.00）、万元（1.2万元）、百分比（-32%、0.32）、块/元（100块/100元）、整数分↔元（12000分=120元、100元=10000分）；日期/时间 token（YYYY-MM-DD、YYYY年M月、ISO 时刻、H:M:S）先剥离不误判；伪造数字（99,999.00 / 5 / 1.3万 / -42% / 200元）全拦；嵌套 facts（groups[]/items[]）递归搜索；端到端「改数字→重生成一次(共2次润色)→仍不过→降级模板+审计 HALLUCINATION_BLOCKED」真走 LLM（calls['classify']==1 且 calls['polish']==2）；写路径走同一套判据。
  - 权限/审计/安全：通过。铁律 2 落地（回执每个数字必须能在 facts 找到出处）；日期时间不算业务数字（卡 09 语义保持）；facts 用 Decimal 归一化（禁 float 精度坑，0.1 元这类不踩）。
RISKS:
  1. 分↔元容差偏宽（worker 待拍板①，spec 固有歧义）：裸数字/元/块展开成 {值, 值×100} 两形态，且 `_RATIOS` 含 ÷100（0.01），使「单位写错」漏报——回执「10000元」会被当成「10000分」放行（事实是 100 元）。根因是 §7「为其百分数表达」（×100）与「元↔分」（也是 ×100）语义同形、无法区分。属 §7 口径固有限制，非实现 bug，但若评审抠「单位写错必须拦」，需收紧容差（建议后续 SPEC-CHANGE 明确「元↔分只在带单位时换算、百分数只在带 % 时换算」）。
  2. 符号不校验（待拍板③）：§7 明写「绝对值相等」，故「-32%」说成「32%」不拦。当前占比/环比类负数与正数混写不会被抓，属 §7 口径。
  3. 裸「万」不处理：`_UNIT` 正则只认「万元」（带元），不认裸「万」；模块 docstring 表写「1.2万 → 12000/1200000」实际代码对「1.2万」（无元）会按 1.2 处理（测试只覆盖「1.2万元」带元）。建议把 docstring 改成「1.2万元」或补裸「万」分支。
  4. 序号按数字处理（待拍板④）：「R3」「第3档」这类序号里的数字会进 `_NUMBER` 被当业务数字，若 facts 里没有对应 3 可能误伤。当前回执模板规避了带序号的写法，但若未来回执出现「第3档」需留意。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 831 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 831 passed、第 5 段「84 passed」、`全部通过 ✅`
  - `git status --short` → 干净（本卡 3 文件已提交 f100002，无漂移）
  - 变异①（verify_numbers 恒 True）→ 6 FAIL（已还原）；变异②（万元×1）→ 1 FAIL（已还原）；变异③（templates.verify_numbers 恒 set()）→ 1 FAIL（已还原）
  - 还原后复跑 831 passed；grep 确认 `return (not offenders, offenders)` / `Decimal(10000)` / `return set() if passed` 均恢复
VERDICT_REASON: §7 数字校验器唯一实现、签名冻结一致、千分位/万元/百分比/块元/分↔元归一化全覆盖、伪造数字拦截与重生成-降级-审计链路真实（变异实测真报警），无 MUST_FIX；仅分↔元容差偏宽等 4 条非阻塞 RISK（多为 §7 口径固有）。
