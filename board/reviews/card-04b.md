# card-04b 审核裁决（聊天裁决补记）

> ⚠ 本文件是**聊天裁决补记**：card-04b 的裁决已于 2026-09-14 在房间以聊天形式给出（PASS，4 RISK），当时未落盘 `board/reviews/card-04b.md`。后经排查发现 board/reviews/ 存在 05/04b/05b 三处断档，故由审核师本人按原聊天裁决补记本文件。EVIDENCE 全部来自当时真实跑出的输出。

```
VERDICT: PASS
CARDS: card-04b
REVIEWED_DIFF: 未提交工作区 13 文件（6 改 + 7 新）：M tools/transfer.py(-83/+74)、data/dao.py(+19)、tools/query.py、tests/test_tools_query.py、tests/test_tools_transfer.py、tests/test_dao.py；?? tools/_query_common.py(113)、tests/conftest.py(281)、test_query_analysis.py(210)、test_query_common.py(84)、test_query_report.py(161)、test_tools_transfer_aa.py(125)、test_tools_transfer_execute.py(188)
CHECKED:
  - 接口一致性：通过。T1–T9 公开签名与 data 字段名零改动（抽共享只把 _ok/_fail/_invalid/_dao_reject/_money/_money_facts/_owned_account_ids/require_owned/ToolError/current_user_id 收敛成单份）。
  - 越界改动：通过（有一处需追认）。卡明文范围 8 文件，实际多出 5 个新文件（conftest + 4 拆分测试），是「678/604 拆 ≤300」的必然结果；依赖单向 _query_common → data/+schemas，无反向 import。
  - 测试真实性：通过，含 2 条变异抽查（详 EVIDENCE）。拆分没丢用例：旧 query 50 + transfer 46 = 96 个 test 函数，新 55 + 47 = 102，丢失=0、新增 6。
  - 边界与异常：通过。TOCTOU 修复到位——execute_transfer 整体在 _TOKENS_LOCK 临界区，state 翻转挪进事务、_rollback_token 失败回退；update_account_balance 金额整数分、balance/available 同步、未知账户→None 无副作用、_writing 靠 in_transaction 加入外层事务。
  - 权限/审计/安全：通过。get_payee 不做归属判断（正确分层）、require_owned 仍在工具层；DAO 原语参数化无注入；消掉 dao.connection() 直查（TODO dao-05b 闭环）。
RISKS:
  1. 300 行没真正解决：4 文件仍超——query.py 418 / transfer.py 452 / dao.py 393 / test_dao.py 525。卡明文要求（拆 2 测试文件 ≤300）达标，但「抽共享」只降了 30–80 行（后续 card-05b 拆源文件收口）。
  2. 真并发用例靠 monkeypatch data.db.connect：生产连接 check_same_thread 默认 True，两线程复用同一连接会 ProgrammingError；TOCTOU 锁本身正确（变异已证），但多线程需 data/db.py 开 check_same_thread=False。
  3. VELOCITY_WINDOW_MINUTES 同名不同义未合并（query=60 只读检测 / transfer=10 写降级），未动正确（后续 05b 拆名）。
  4. 5 个新文件超出卡明文范围，建议追认。
MUST_FIX: 无
EVIDENCE:
  - uv run pytest -> 377 passed, exit 0（354 原样 + 23 新增）；bash scripts/verify.sh -> 全部通过 ✅
  - 变异A：transfer.py 去 _TOKENS_LOCK -> test_execute_is_idempotent_under_real_concurrency FAIL（cannot start a transaction within a transaction），已还原
  - 变异B：dao.py update_account_balance 去 _cents -> test_update_account_balance_rejects_illegal_delta 6 条全 FAIL，已还原
  - 拆分完整性：git show HEAD: 旧两文件 vs 新拆分文件，comm -23 丢失=0（旧 96 → 新 102，+6）
  - grep MUTATION 无残留；uv run pytest 377 恢复
VERDICT_REASON: card-04b 六项范围达标——共享 helpers 单份化（is 断言钉住）、拆测试 ≤300、DAO 两个写原语经变异抽查真实、TOCTOU 锁+事务内翻转真修复且并发用例有牙齿、354 原样绿零丢用例；唯一遗留是「300 行只治了测试没治源文件」+「并发用例靠 patch 连接」，记 RISK 待 05b 或口径定夺。
```
