VERDICT: PASS
CARDS: card-06
REVIEWED_DIFF: 8 个文件 —— tools/subscription.py(+282)、tools/card.py(+272) 为未跟踪新文件；tests/test_tools_subscription.py(+128)、tests/test_tools_subscription_cancel.py(+256)、tests/test_tools_card.py(+158)、tests/test_tools_card_guards.py(+202)、tests/test_tools_card_audit.py(+124) 为未跟踪新文件；tests/conftest.py(修改，288 行)。全部 ≤300 行。指纹与交付一致（subscription d35e893c… / card aa8c417e… / 5 测试 + conftest 逐字比对）。
CHECKED:
  - 接口一致性：通过。T10 data=items[{id,merchant,amount,cycle,next_charge_date}]（5 字段冻结，僵尸标注只走 facts）、T11 data={sub_id,status,effective_date}、T12 data=card 快照（DDL 9 列）与规格 §2 逐字对齐；ErrorCode 复用 schemas 的 FORBIDDEN/TOKEN_EXPIRED/INVALID_STATE/INVALID_ARGUMENT/NOT_FOUND，未新增取值。
  - 越界改动：无。范围只 8 个文件；tools/_query_common.py 只 import 未改（符合范围门禁）；confirm_ref 机制落 subscription.py、card.py 单向 import（06b 抽 tools/_confirm.py 待办已记）。
  - 测试真实性（变异抽查 3/3 真报警，均已还原、指纹恢复）：① check_confirm_ref「不存在 → FORBIDDEN」改回 TOKEN_EXPIRED → 8 条 FAIL（test_bogus_confirm_ref_is_forbidden ×4、test_t11_unknown_ref_is_forbidden ×3、test_t11_never_accepts_a_plain_string）；② _l3_facts 删 revocable → 2 条 FAIL（test_l3_facts_carry_the_delay_window[unlock/report_lost]）；③ 禁用 reject_audit → 1 条 FAIL（test_foreign_card_is_forbidden_and_untouched）。
  - 边界与异常：通过。幂等（同 ref 重放同结果且不再写审计、重放仍校验 OTP、失败前置不消费凭证）；状态机（lost 卡 unlock → INVALID_STATE 且 message 含「补卡」专用分支，非通用「未锁定」分支）；金额整数分禁浮点；OTP 明文不入库；原子性（monkeypatch insert_audit 抛错 → 状态翻转整体回滚）；4 条口径（L3 facts=l3_delay_seconds/revocable/l3_window_phase/to_human、FORBIDDEN 边界、FORBIDDEN 写 rejected 审计、僵尸订阅锚 AS_OF）全部落实。
  - 权限/审计/安全：通过。越权/伪造 → FORBIDDEN 且写 audit_log.result='rejected' 留痕；每个成功动作恰一条审计（intent 用规格意图清单、tier 正确、重复调用不新增）；L3 双因子（confirm_ref + OTP）缺一 FORBIDDEN；require_owned 兜底他人资源；OTP 顺序在幂等快照之前（重放带错 OTP → FORBIDDEN）。
RISKS:
  1. 过时注释：tools/subscription.py:236 的 cancel_subscription docstring 仍写「不存在/过期 TOKEN_EXPIRED」，但 check_confirm_ref 已按口径改为「不存在 → FORBIDDEN、仅超时 → TOKEN_EXPIRED」。影响：安全边界（伪造 vs 超时）文档自相矛盾，后人可能照注释「修」错。建议：一行改成「不存在/非本人/绑定不符 → FORBIDDEN；仅超时 → TOKEN_EXPIRED」。
  2. apply 的 data 形状偏离「card 快照」：id=None、card_no_mask=None、status='pending_review'（非 DDL 枚举）+ 额外 ref_card_id/request_id，共 11 键（其余 action 是 9 键 DDL 列）。属铁律 4「不得改变返回结构」的边界情况——规格未定义 apply（无申请表、无建卡原语）。已按 mock 登记（同 T16），但建议后续走 SPEC-CHANGE 明确 apply 返回口径，或补 DAO 建卡原语。
  3. _zombie_ids 直接 dao.list_txn 全量取 3 个月窗口、无 category 过滤且未做 TOO_MANY_ROWS 兜底（窗口 >500 条会静默截断、漏判僵尸）。种子数据 ~150 条/3 月不触发，属潜在边界。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 520 passed（独立复跑 4 次全绿）
  - `bash scripts/verify.sh` → 520 passed、3/5–5/5 SKIP（卡11/09/12-13）、「全部通过 ✅」
  - `sha256sum` 8 文件 → 与交付指纹逐字一致（subscription d35e893c… / card aa8c417e… / 2b9e15ed… / 63b2651b… / 7ffe0dba… / 591d7251… / 5012932c… / 4fa70b67…）
  - 变异①（FORBIDDEN→TOKEN_EXPIRED）→ 8 FAIL（已还原）；变异②（删 revocable）→ 2 FAIL（已还原）；变异③（禁用 reject_audit）→ 1 FAIL（已还原）
  - 还原后复跑 520 passed、指纹恢复
VERDICT_REASON: 8 文件范围干净、520 测试稳定全绿、4 条口径全部落实且变异抽查证伪有效、越权/幂等/原子性/审计完整性到位，无 MUST_FIX；仅 3 条非阻塞 RISK（过时注释 / apply mock 形状 / 僵尸窗口潜在截断）。
