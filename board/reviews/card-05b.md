# card-05b 审核裁决（聊天裁决补记）

> ⚠ 本文件是**聊天裁决补记**：card-05b 的裁决已于 2026-09-14 在房间以聊天形式给出（PASS，3 RISK），当时未落盘 `board/reviews/card-05b.md`。后经排查发现 board/reviews/ 存在 05/04b/05b 三处断档，故由审核师本人按原聊天裁决补记本文件。EVIDENCE 全部来自当时真实跑出的输出。

```
VERDICT: PASS
CARDS: card-05b
REVIEWED_DIFF: 未提交工作区 13 文件（8 改 + 5 新）：M tools/query.py(219)、transfer.py(287)、schemas.py(198)、_query_common.py(130)、data/dao.py(239)、tests/test_dao.py(253)、test_query_analysis.py(228)、test_tools_transfer.py(196)；?? data/_dao_core.py(209)、tools/_query_analysis.py(209)、tools/_transfer_risk.py(106)、tests/test_dao_core.py(260)、test_transfer_risk.py(96)
CHECKED:
  - 接口一致性：通过。T1–T9 公开签名与 data 字段名零改动（纯拆文件 + 模型归位 schemas + 会话 helper 归 _query_common）。
  - 越界改动：通过。范围 = 卡 663edbc 扩后 11 文件，实际 13 文件（多出 test_dao_core.py、test_transfer_risk.py 两个拆分测试文件，是「test_dao.py 525→≤300」的必然结果）。
  - 测试真实性：通过，含 2 条变异抽查（详 EVIDENCE）。新增 test_split_modules_never_import_their_callers 用静态断言钉住依赖方向（验收门②）。
  - 边界与异常：通过。连接全局 + connect_db/close/connection/_one/_many/_writing/_insert/_apply_update + 全部校验 helper 整块在 _dao_core.py，dao.py 只留 16 公开函数 + re-export，无「两份连接状态」；_insert 的 skip_ts 布尔收窄原样保留。
  - 权限/审计/安全：通过。TOCTOU 锁仍在临界区包住检查→扣款→翻转；update_account_balance 整数分校验保留；无新权限/审计面。
RISKS:
  1. 新增 12 条测试集中在 test_transfer_risk.py / test_dao_core.py（抽出的风控/DAO 逻辑），抽验了 TOCTOU 锁与 update_account_balance 两条关键变异均真实，但没对全部 12 条逐一变异——因 377 原样绿 + 0 丢用例已保证行为保持，残余风险低。
  2. seed.py 恰好 300 行（≤300 压线），不在本卡范围、未动，下张卡加种子数据会立刻破线。
  3. 工作区当时尚未提交，提交需精准显式列 13 文件、禁 git add -A。
MUST_FIX: 无
EVIDENCE:
  - 源文件行数（全部 ≤300）：query 219 / transfer 287 / dao 239 / _dao_core 209 / _query_analysis 209 / _transfer_risk 106 / schemas 198 / _query_common 130 / seed 300；测试文件全部 ≤300
  - uv run pytest -> 389 passed, exit 0（377 原样 + 12 新增）；bash scripts/verify.sh -> 全部通过 ✅
  - 零丢用例：comm -23 对比 HEAD(7d86b8d) vs 工作区 → 旧 178 / 新 190，丢失=空、新增 12
  - 验收门②：grep 三个新源文件 + _dao_core → 零反向 import；VELOCITY 拆名：_query_analysis.py VELOCITY_MINUTES_T4=60（T4 只读）、_transfer_risk.py VELOCITY_MINUTES_WRITE=10（§5 写降级），阈值未搞反
  - 变异C：transfer.py 去 _TOKENS_LOCK -> test_execute_is_idempotent_under_real_concurrency FAIL，已还原
  - 变异D：dao.py update_account_balance 去 _cents -> test_update_account_balance_rejects_illegal_delta 6 条全 FAIL，已还原
  - grep MUTATION 无残留；uv run pytest 389 恢复
VERDICT_REASON: card-05b 四条验收门全达标——连接全局随写原语整块进 _dao_core 无双连接态、依赖单向禁反向、VELOCITY 两名字阈值未搞反、178→190 零丢用例且 TOCTOU/DAO 两条变异经重跑证明真有效；源文件与测试文件首次全部 ≤300，工具层清理收口。
```
