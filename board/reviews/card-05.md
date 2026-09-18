# card-05 审核裁决（聊天裁决补记）

> ⚠ 本文件是**聊天裁决补记**：card-05 的裁决已于 2026-09-14 在房间以聊天形式给出（PASS，5 RISK），当时未落盘 `board/reviews/card-05.md`。后经排查发现 board/reviews/ 存在 05/04b/05b 三处断档，故由审核师本人按原聊天裁决补记本文件。EVIDENCE 全部来自当时真实跑出的输出。

```
VERDICT: PASS
CARDS: card-05
REVIEWED_DIFF: 2 files changed, 1084 insertions(+) —— tools/transfer.py(480) + tests/test_tools_transfer.py(604)，commit 155804b
CHECKED:
  - 接口一致性：通过。T6–T9 签名与 data 字段名和规格 §2 逐字一致——resolve_payee→candidates[{id,name,masked_phone}]+ambiguous、preview_transfer(payee_id,amount,schedule=None,split_with=None)→preview_token/fee/tier/requires_otp/limits、execute_transfer(preview_token,otp=None)→txn_id/amount/payee_name/balance_after、create_aa_request(payee_ids,amount)→request_id/per_person_amount，由 test_data_keys_are_frozen_to_the_spec 钉住。
  - 越界改动：通过。仅 transfer.py + test_tools_transfer.py 两文件；仅从 dao.connection() 直查两处（payee 按 id、账户扣款），参数化 SQL 无注入，标 TODO(dao-05b)。
  - 测试真实性：通过，含 4 条变异抽查全真报警（详 EVIDENCE）。71 条用例含正常/边界/非法参数，金额/余额/行数多处独立原生 SQL 复算。
  - 边界与异常：通过。金额纯整数分（_money divmod、无 float）；幂等只扣一次款且带反向用例防「一律去重」；多步事务原子回滚（扣款+流水+审计一事务，_writing 靠 in_transaction 加入不嵌套 BEGIN）；TOKEN_EXPIRED/TTL 300s；AA 余数给发起人合计精确相等；OTP 不进日志/facts/回执。
  - 权限/审计/安全：通过。越权 fail-closed——resolve_payee 按 user 过滤、preview/execute 收款人+账户归属断言、execute token 归属校验，越权一律 FORBIDDEN 且 data/facts 空、他人账户分毫不动；execute 零 LLM 调用（静态断言）；error_code 全走枚举零硬编码。
RISKS:
  1. 幂等并发是线程级 TOCTOU：token state 检查在事务外、_TOKENS 模块级 dict，两线程同时 execute 同 token 会双重扣款。测试只覆盖顺序重复，无真并发用例（后续 card-04b 修复）。
  2. fee 恒为 0：规格 §2 T7 要 fee 但未定义费率，未编造费率（正确），需后续口径。
  3. _payee_by_id/_debit 直查 dao.connection() 绕过 DAO 原语（TODO dao-05b），参数化无注入，后续 04b 补 get_payee/update_account_balance 收口。
  4. 规格 §5 自相矛盾：new_payee 既在 L2 基础条件又在降级因子清单，worker 选「基础档已体现不再升档」折中并文档化（后续 SPEC-CHANGE bd8fa64 修 §5 去重）。
  5. _money 硬编码 100（query.py 用 PCT_TOTAL），有跨模块一致测试钉住（后续 04b 收进 _query_common 单份）。
MUST_FIX: 无
EVIDENCE:
  - uv run pytest tests/test_tools_transfer.py -> 71 passed；全仓 uv run pytest -> 354 passed；bash scripts/verify.sh -> 全部通过 ✅
  - 变异①：execute 幂等短路改 if False -> test_execute_twice_with_the_same_token_debits_only_once FAIL（二次扣款），已还原
  - 变异②：_history_mean_cents 的 // 改 / -> test_no_floats_anywhere_in_data_or_facts FAIL（float 进 facts），已还原
  - 变异③：_debit 加 conn.commit() 提前提交 -> test_execute_is_atomic_when_a_step_fails FAIL（rollback 报 no transaction is active），已还原
  - 变异④：resolve_payee 去 user 过滤 -> test_resolve_payee_hides_other_users_payees FAIL（他人收款人泄漏），已还原
  - grep MUTATION 无残留；git diff --stat tools/ 空（transfer.py 逐字节还原）
VERDICT_REASON: T6–T9 接口与规格 §2 逐字对齐、幂等/禁浮点/事务原子/越权四条红线经双向变异抽查证明真实有效、金额纯整数分、越权 fail-closed 不泄漏，无 MUST_FIX；唯一实质顾虑是并发幂等的 TOCTOU 窗口（单线程 demo 不触发、worker 已披露），记 RISK 待后续卡收口。
```
