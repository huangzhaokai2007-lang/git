# card-06b 审核裁决（技术待办）

> card-06b 是分析师派给本房间 worker 的技术待办清理（非正式卡，无 card-06b.md 卡文件）。三项已做 + 两项刻意 defer。审核师按新政策「每张卡审完直接落盘」出具本裁决。

```
VERDICT: PASS
CARDS: card-06b（技术待办）
REVIEWED_DIFF: 6 files changed, 79 insertions(+), 20 deletions(-) —— tools/transfer.py(-4)、tools/subscription.py(+20/-6)、tools/_query_common.py(+18)、tools/_query_analysis.py(+4/-7)、tests/test_query_common.py(+33/-1)、tests/test_query_report.py(+4)
CHECKED:
  - 接口一致性：通过。三项均为内部清理/兜底，T1–T12 公开签名与 data 字段名零改动。
  - 越界改动：通过。6 文件全在 06b 车道，未碰 card-07 文件（wealth.py/cross_scene.py/_wealth_risk.py 及 4 个测试），git status 已核实。
  - 测试真实性：通过。facts 逐字断言自带「有牙齿」自证用例（test_verbatim_assertion_catches_a_magnitude_error 用 pytest.raises 钉住量级错必红），并独立变异验证（详 EVIDENCE）。
  - 边界与异常：通过。month_windows 单份化行为等价（_next_month 内联，test_query_analysis 仍钉窗口切分）；_window_flows 单窗超 MAX_LIMIT → TOO_MANY_ROWS（fail-closed，不再静默截断）；删重复 _now 零行为。
  - 权限/审计/安全：通过。无新权限/审计面；TOO_MANY_ROWS fail-closed 是防御性改进。
RISKS:
  1. item 2（僵尸订阅 TOO_MANY_ROWS）**新行为路径无测试**：subscription._window_flows 的「单窗超 500 行 → TOO_MANY_ROWS」没有用例钉住，现有 test_t10_zombie_* 只覆盖正常窗口。影响：若将来误删 total_count > MAX_LIMIT 检查，会静默退回「截断误判僵尸」老 bug 且无测试报警。建议：下一轮补一条「单月塞 501 行 → list_subscriptions 返回 TOO_MANY_ROWS」+ 正向「≤500 行正常判僵尸」。
  2. 两项行为变更（T4 陌生商户、T2 total_count）刻意 defer，正确——都是会改既有断言的语义变化。其中 T2 会改 04-06 反复审过的 fail-closed 语义，动之前须 @reviewer 明确点头（worker 已提出此门槛，审核师认可）。
MUST_FIX: 无
EVIDENCE:
  - uv run pytest -> 643 passed（含并行会话 card-07），exit 0；bash scripts/verify.sh -> 全部通过 ✅
  - 变异E：assert_facts_verbatim 退化成归一化比较（norm 剥千分位/小数点）-> test_verbatim_assertion_catches_a_magnitude_error FAIL（DID NOT RAISE，量级错被放过），已还原
  - git diff --stat（06b 6 文件）-> 79 insertions(+), 20 deletions(-)
  - grep MUTATION 无残留；uv run pytest 643 恢复
VERDICT_REASON: 06b 三项全部正确——删重复 _now 零行为、僵尸订阅兜底从「静默截断」改成 fail-closed TOO_MANY_ROWS、facts 逐字断言经变异证明真能挡量级错；唯一缺口是 TOO_MANY_ROWS 新路径没配测试，记 RISK 下一轮补，不阻塞。
```
