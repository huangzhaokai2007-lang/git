# card-04 审核裁决（事后复验补记）

> ⚠ 本文件是**事后复验补记**：账本曾记录「card-04 审核 PASS」（决策记录 20:41），但当时未落盘 `board/reviews/card-04.md`，且 `logs/` / `board/.tmp/` 零 card-04 痕迹。审核师未背书一个无实物、自己无法证实的 PASS，故对 `745d960` 重新独立复验后补记本文件。以下 EVIDENCE 全部来自本次复验真实跑出的输出，非转述账本。

```
VERDICT: PASS
CARDS: card-04
REVIEWED_DIFF: 复验对象 = commit 745d960（card-04 交付）：3 files changed, 1317 insertions(+) —— tools/query.py(497) + tools/schemas.py(142) + tests/test_tools_query.py(678)。此后 schemas.py +3 行（633da2d ErrorCode 补 3 码）、query.py 1 行注释（21f8344）均为独立小 commit，不改变 card-04 行为代码。
CHECKED:
  - 接口一致性：通过。T1–T5 签名与规格 §2 逐字一致（get_balance(account_type) / list_txn(date_from,date_to,category=None,min_amount=None,limit=50) / analyze_spending(period,group_by='category') / detect_anomalies(period) / generate_bill_report(period,kind='monthly')）；ToolResult 5 字段（ok/data/error_code/message/facts）与规格 §2 冻结结构一致；data 字段名（balance/available/as_of、items/total_count、groups[{key,amount,pct}]/total/vs_prev_pct、items[{txn_id,reason,severity}]、markdown/summary_numbers）逐一与规格表对齐，且由 test_tool_result_and_data_keys_are_frozen 钉住。
  - 越界改动：通过。仅 tools/query.py + tools/schemas.py + tests/test_tools_query.py 三文件，只 import data/(DAO)，未动 guard/agent/interfaces/data。
  - 测试真实性：通过，含 4 条红线变异抽查全真报警（详 EVIDENCE）。96 条测试含正常/边界/非法参数三类，金额、条数、均值、百分比多处用独立原生 SQL 复算（不复用被测算法）。
  - 边界与异常：通过。金额全程整数分（_money 用 divmod、无 float）；回执 message 与报告 markdown 的每个数字都在 facts 里（幻觉红线）；单月流水超 500 报 TOO_MANY_ROWS 不悄悄少算；vs_prev_pct 整数百分比、上期无支出返 None；错误消息一律不含数字（防污染数字校验器）。
  - 权限/审计/安全：通过。L2 越权 fail-closed——他人账户 id 更小时 get_balance 返 FORBIDDEN 且 data/facts 全空（不泄漏任何数字），list_txn/analyze/detect/report 均只暴露「能确认归属」的流水；set_current_user 驱动归属判定，越权订阅不进入报告。
RISKS:
  1. facts 归一化弱于「逐字一致」：assert_covered 的 numbers() 会剥小数点/千分位，能挡「多一个数字」但挡不住量级错（如 123.45 写成 123.46 会被当同一数字放行）。影响：generate_bill_report 的 markdown 若被改成量级错误的数字，当前断言可能不红。建议：卡 13 facts_check 做逐字断言前，给金额类 facts 补「_yuan 串逐字相等」的强化断言。非阻塞。
  2. T2 fail-closed 时 total_count 会小于真实值：当同名类型下存在他人账户且 id 更小，DAO 优先返回他人行 → 本人该类型账户取不到 → list_txn 只统计信用账户。影响：单用户 demo 不触发；多用户/越权扩展时 total_count 偏低。建议：多用户场景扩展时复核 total_count 口径（账本台账已登记）。非阻塞。
  3. 本文件为事后复验补记，复验对象是 745d960 的行为代码；自该 commit 起 query.py/schemas.py 仅有 1 行注释 + 3 个枚举值的非行为变更（已单独 commit）。评审若按「每次 PASS 必落盘」追溯，需知原始 20:41 的审核无盘上实物、本文件是补记。
MUST_FIX: 无
EVIDENCE:
  - `git show --stat 745d960` -> 3 files changed, 1317 insertions(+)（query.py 497 / schemas.py 142 / test_tools_query.py 678）
  - `uv run pytest tests/test_tools_query.py` -> 96 passed, exit 0
  - `uv run pytest`（全仓）-> 354 passed, exit 0
  - 变异①：query.py 余额 message 硬编码「另 9999 元」-> test_reply_message_numbers_are_all_in_facts[get_balance] 与 test_get_balance_matches_the_account_row 双双 FAIL（幻觉红线抓到 9999 不在 facts），已还原
  - 变异②：require_owned 置空（return 短路）-> test_get_balance_refuses_a_foreign_account / test_session_user_drives_the_ownership_decision / test_require_owned_contract_for_cards_and_subscriptions / test_foreign_account_does_not_rescue_the_balance_query 共 4 条 FAIL，已还原
  - 变异③：_owned_account_ids 放行他人（去掉 user_id==current_user_id()）-> test_foreign_txns_never_leak_into_any_tool 与 test_foreign_account_does_not_rescue_the_balance_query FAIL（他人流水/账户泄漏），已还原
  - 变异④：_vs_prev_pct 改用 float 除法 -> test_no_floats_anywhere_in_data_or_facts[analyze_spending/generate_bill_report] 与 test_analyze_spending_vs_prev_pct_is_recomputed_independently FAIL（浮点 + pydantic 校验拦截），已还原
  - 还原后：`grep -rn MUTATION tools/query.py` 无残留；`git diff --stat tools/` 为空（query.py 逐字节回到提交态）；`uv run pytest` 354 passed
VERDICT_REASON: 对 745d960 重新复验后确认 card-04 交付合格——T1–T5 接口与规格 §2 逐字对齐、4 条红线（幻觉/越权/禁浮点/错误消息无数字）经双向变异抽查证明真实有效、越权 fail-closed 不泄漏数字、金额纯整数分，无 MUST_FIX。
```

_session: 复验由审核师（reviewer）于 2026-09-14 执行；2 条非阻塞 RISK 与账本台账既有登记一致。
