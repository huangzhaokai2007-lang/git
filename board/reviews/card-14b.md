VERDICT: PASS
CARDS: card-14b
REVIEWED_DIFF: 9 个文件（已提交 5235353，本审为提交后复核）—— data/schema.sql（+idempotency +rate_limit 两表、factor 枚举 +unauthorized_resource）、data/db.py（TABLES 10→12）、data/_dao_core.py、data/dao.py（+4 原语 get_payee/get_idempotent/insert_idempotent/incr+count_rate_limit）、docs/01-接口规格.md（§DDL 加 2 表 + factor 注释）、guard/tool_guard.py（check_write_rate 先计数再校验 + idempotent_execute 落库快照）、tests/test_db.py（冻结守卫 10→12）、tests/test_dao_core.py、tests/test_tool_guard.py（+限流/幂等/重启/重放/越权双写用例）。基线 859=852+7 零回归。
CHECKED:
  - 接口一致性：通过。§DDL 两新表字段（idempotency: token PK/tool/user_id NOT NULL/result_json/created_at；rate_limit: id AUTOINCREMENT/user_id/tool/ts）与 schema.sql 逐字一致（test_db 冻结守卫已 10→12，`assert len(TABLES)==12` 同步）；factor CHECK 枚举加 unauthorized_resource 后 spec 与 schema 双向同步，卡 14a 发现的「schema≠spec 逐字」断裂已闭合。
  - 越界改动：无。4 个 DAO 原语用 `_writing()` 上下文 + 参数化 `conn.execute`，裸 SQL 留在 data 层（工具层仍只调 DAO，无裸 SQL 下沉）；get_payee 按 id 精确查找，不做归属判断（越权是 guard 的活）。
  - 测试真实性（变异抽查 4/4 真报警，均已还原）：① 限流阈值 `used > RATE_MAX_WRITES`→`>999` → test_sixth_write_in_window_is_rejected FAIL（用例写死字面量 5/6，不复用被测常量，防自证）；② 重放路径加 `note_write` → test_replay_does_not_count_toward_the_rate_limit FAIL；③ 幂等落库注释掉 insert → test_idempotent_replay_returns_same_result_and_runs_once + test_idempotency_snapshot_survives_a_restart 双 FAIL；④ require_owned 加 `note_write`（只读也计数）→ test_reads_do_not_touch_the_limiter FAIL。还原后 grep 无残留、859 全绿。
  - 边界与异常：通过。「先计数再校验」顺序正确（note_write 在判定前，被拒的第 6 次尝试也落 rate_limit 行，test 断言 count==6）；滚动窗口 `ts >= now-60s` 含边界、窗口滑走后重新放行；只读路径（require_amount_cents/require_owned）零计数；幂等重放命中落库快照直接返回（producer 只执行一次，`(replayed,replayed_again)==(False,True)` + `len(calls)==1`）。
  - 权限/审计/安全：通过。越权双写审计 + risk_event（factor=unauthorized_resource 现在能写进，枚举已补齐）有集成断言；INSERT OR IGNORE 防同 token 重复落行；risk_event 写失败只告警不吞拒绝。
RISKS:
  1. `idempotent_execute` 是「先查后写」无锁（TOCTOU）：并发同 token 两个请求都可能 `get_idempotent`→None→都跑 producer→都 insert（INSERT OR IGNORE 只挡重复行、不挡重复执行）。当前未接线（transfer 内存 _TOKENS 切幂等表是 14b-2），接线时应复用 card-05 的 `_CONFIRM_LOCK`（threading.Lock）把「查→产→写」包成临界区。
  2. 限流与幂等的调用顺序：docstring 写「先调 idempotent_execute、未命中再调 check_write_rate」，意味着 producer（写副作用）先于限流判定执行——超限用户的那次写会「先执行、再被拒」。14b-2 接线时需把限流判定前移到 producer 之前（同时保持「重放不计数」），否则限流防不住写副作用。
  3. `require_payee_exists`（已改为 id 精确查找）仍未接线到 preview_transfer——14b-2 收尾项。
  4. 测试名 `test_statements_split_into_ten_complete_statements` 已过时（实为 12 条语句），纯命名瑕疵，不影响断言。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 859 passed（独立复跑）
  - `bash scripts/verify.sh` → 859 passed、第 3 段 32 passed、第 5 段 84 passed、`全部通过 ✅`
  - 变异①（限流阈值 >999）→ 1 FAIL；变异②（重放 note_write）→ 1 FAIL；变异③（注释 insert_idempotent）→ 2 FAIL；变异④（只读 note_write）→ 1 FAIL（均已还原）
  - 还原后 grep 无 `变异`/`used > 999` 残留、`git status` 无 guard/tool_guard.py 改动（工作区 6 个 M + 1 个 ?? 为 14b-2 并发收尾，见下）
VERDICT_REASON: 限流（先计数再校验/滚动 60s/只读与重放不计数）、幂等落库（INSERT OR IGNORE/重启有效）、SPEC-CHANGE 加 2 表 + factor 枚举三件套落实，4 次变异抽查证伪有效，859 基线零回归，无 MUST_FIX；仅幂等 TOCTOU、限流-幂等调用顺序 2 条接线期 RISK（属 14b-2）。

注：审核期间检测到 14b-2 并发收尾（tools/card.py + query.py + subscription.py + transfer.py + tests/test_tools_card_audit.py + test_tools_subscription_cancel.py 被改，另有未跟踪 scripts/card14b_name_sites.py），当前 pytest 仍 859 绿。此为 14b-2 的「39 处补 tool 名 + transfer 切幂等表」在途改动，不影响本卡（card-14b 已提交且 859 绿）的 PASS 结论，但请 analyst 知悉。
