# 团队进度账本（分析师维护，人也可以看）

> 格式：每张卡一段，由分析师追加。人也可以在这里写"人类批示"。

## 已接受的卡（存档点）

| 卡 | commit | 审核 | 备注 |
|---|---|---|---|
| card-00 | cd97242 | 人工 | 骨架 + 施工包 |
| card-00b | e307353 | 人工 | .hermes.md / docs/cards / run-card.sh |
| card-01 | e2c1046 | 未审（审核机制建于其后） | 数据层建库；我事后独立复验：pytest exit 0、schema 10 表与规格一致 |
| card-02 | 0c60abc | PASS（board/reviews/card-02.md，无 MUST_FIX） | 12 个月合成数据；verify 绿。原 5561965 被 reset 重做为干净提交（仅 seed.py + test_seed.py） |
| infra | fb40cbb | 人工 | 三人小组编排器 v2 + 角色定义 + 账本；.gitignore 补 board/.tmp/ |
| card-03 | 166f39d | PASS（card-03.md 无 MUST_FIX + delta review PASS） | DAO 层 14 函数 + 121 测试；verify 绿（185 passed）。审核后增量（volatile 幂等豁免 + 2 测试）经 delta review 确认无幂等漏洞、变异抽查有效 |

## 已知风险台账（同类风险出现 2 次即升级为阻塞）

| 首次出现 | 风险 | 影响 | 状态 |
|---|---|---|---|
| card-01 | `data/db.py` 的 `transaction()` 不可嵌套 | 卡 05 写多步事务时会抛 "cannot start a transaction within a transaction" | 待卡 05 验证（不影响卡 03/04 单条读写） |
| card-02 | `git add -A` 把编排器脚手架混进卡 commit | 提交历史污染 | 已解决：5561965 已 reset 重做为 0c60abc；fb40cbb 已给 .gitignore 补 board/.tmp/，`git check-ignore` 验证生效 |
| card-03 | data/dao.py(371)/tests/test_dao.py(388) 单文件超 300 行，靠"顶层函数空 1 行"硬挤破坏 PEP8 | 评审逐条对代码规范可扣分 | **第 2 次出现（delta review 再报 dao.py 368→371 / test_dao.py 388→425）→ 升级为阻塞**：card-03b 先抽 data/_dao_core.py 恢复 PEP8 2 空行，再进工具层 |
| card-03 | DAO 读接口（get_card/get_subscription/get_product）不按 user 圈定 | 属正确分层，但 tools/（卡 04-07）若不做资源归属断言则出现越权读他人卡/订阅 | 传导卡 04-07：每个资源查询带 user 归属断言 + 越权单测（规格 L2） |
| card-03 | 审核 PASS 后代码又被改（+volatile 幂等豁免 3 行 + 2 条测试）才提交 166f39d | 审核结论覆盖的是 183passed 版，最终提交是 185passed 版，差额未经审核师复核 | **已闭环**：审核师 delta review = PASS（无 MUST_FIX，变异抽查 2 处均真报警） |
| card-03（delta） | `test_retry_...` 里 txn 段用显式 STAMP 而非 None，insert_txn 自生成 ts 的豁免路径未被直接覆盖（仅 risk_event/audit 覆盖） | 若未来单独改坏 insert_txn 的 ts is None 分支，该测试不红 | 待办 DAO 补测：补 insert_txn(ACCOUNT, None, ...) 连续两次幂等用例（**不属卡 04 范围**，留作 DAO 补测小卡） |
| card-03（delta） | 「自生成 ts + 异内容 → 冲突」无直接用例 | 缺一条显式回归钉住该组合 | 待办 DAO 补测：加 ts=None、同 id、异 amount 必须 ValueError 用例（**不属卡 04 范围**） |
| card-03（delta） | 幂等比对用 `existing[name] != value` 直接比较，依赖列类型读回后与写入端一致 | 若将来扩表写入类型与 DDL 存储类型错位，会恒判「不同」误报冲突 | 待办：后续扩表时留意列类型一致性（非阻塞） |
| card-03（delta） | `_insert` 的 volatile 是通用列名元组，当前仅被 ts 用；未来误把业务列（amount）写进 volatile 会静默漏比对 | 同 id 异金额被当同一条吞掉（幂等静默失效） | **card-03b 收窄**：改成布尔 skip_ts，或断言 volatile ⊆ {"ts"} |
| card-03（delta） | retry 测试 `monkeypatch.setattr(dao, "datetime", Clock)` 绑定「DAO 用模块级 now() 取时间」的实现细节 | 改时间来源（sqlite CURRENT_TIMESTAMP / time.time()）后该测试假绿/假红 | 传导：将来动时间来源时同步改该测试 |

## 人类批示

- （空）

## 决策记录 2026-09-12 15:34

事实（自己查到的）：
  - 最新存档点：fb40cbb infra: 三人小组编排器 v2 + 角色定义 + 账本（HEAD）
  - 工作区：clean（nothing to commit, working tree clean）
  - verify：绿。64 passed；用例/冒烟/红线三处 SKIP（卡 11/09/12-13 未实现，预期，非回退）
  - 历史澄清：上轮决策记录的 5561965（card-02 被 `git add -A` 污染）已被 reset 重做为 0c60abc（干净：仅 data/seed.py + tests/test_seed.py）；fb40cbb 已给 .gitignore 补 board/.tmp/ 且 `git check-ignore` 验证生效。R2 已闭环。
  - 最近审核：card-02 = PASS（board/reviews/card-02.md，无 MUST_FIX）

进度判断：
  - 已完成卡：card-00、card-00b、card-01、card-02（+ infra fb40cbb）
  - 卡在哪：数据层剩最后一块 card-03（DAO 层），工具层尚未开始
  - 风险累积：R2（提交污染）已闭环归零；R1（transaction 不可嵌套）仍待卡 05 验证，不影响卡 03 单条读写

NEXT_CARD: 03
MODEL: deepseek-flash
ACTION: run
REASON: 仓库绿 + 工作区 clean + 上轮 fix_first 已由 fb40cbb 落地（.gitignore 挡 board/.tmp/、提交历史已重做干净）。card-03 是纯 CRUD DAO，不涉及权限/风控/注入/编排，用 flash 即可。无阻塞项，直接跑。
NEXT_CARD_WARNING: 提交只 `git add data/dao.py tests/test_dao.py`，禁止 `-A`；DAO 不做权限/风控判断（那是 guard 的活）；insert_txn/insert_audit 是单条写入，别在卡 03 里嵌套事务（会撞 card-01 的 transaction() 不可嵌套风险）。

## 决策记录 2026-09-12 18:46

事实（自己查到的）：
  - 最新存档点：fb40cbb infra（HEAD）；其下 0c60abc card-02 / e307353 card-00b / e2c1046 card-01 / cd97242 card-00
  - 工作区：有未提交改动 —— M board/ledger.md；?? board/progress.html、board/reviews/card-03.md、data/dao.py(371)、tests/test_dao.py(388)
  - verify：绿。183 passed（+119 条 DAO 测试）；用例/冒烟/红线三处 SKIP（卡 11/09/12-13 未实现，预期非回退）
  - 最近审核：card-03 = PASS（board/reviews/card-03.md，无 MUST_FIX，3 条非阻塞 RISK）

进度判断：
  - 已完成卡：card-00/00b/01/02（已 commit）；card-03 代码+测试+审核均完成，但**存档点缺失**——data/dao.py、tests/test_dao.py 仍是 untracked，git 历史无 card-03 commit
  - 卡在哪：card-03 卡在"提交"这一步；工具层（card-04 T1-T5 只读工具）尚未开始
  - 风险累积：无同类风险第 2 次。新增 R3（单文件超 300 行）第 1 次非阻塞；R4（DAO 读接口不按 user 圈定）传导卡 04-07；R2 已闭环归零；R1 仍待卡 05 验证

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: fix_first
REASON: card-03 已 PASS 但存档点缺失（交付文件未提交）。先精准提交 card-03：`git add data/dao.py tests/test_dao.py`（禁止 -A）→ commit，board/ 单独记账提交，再跑 card-04。card-04 是工具层首卡，含 detect_anomalies 风控规则 + generate_bill_report 的 facts/markdown 数字一致性（幻觉校验）→ 必须 v4-pro。
NEXT_CARD_WARNING: card-04 每个数字必须进 facts 包、generate_bill_report 的 markdown 数字与 facts 逐字一致；DAO 读接口不按 user 圈定，工具层给资源查询带 user 归属断言并配越权单测（规格 L2）；阈值常量注明来源（近90天均值3倍 / 23:00-06:00 / 同商户1小时≥3笔）。

## 决策记录 2026-09-12 18:53

事实（自己查到的）：
  - 最新存档点：a3e25e7 board: card-03 记账（HEAD）；其下 166f39d card-03 / fb40cbb infra / 0c60abc card-02
  - 工作区：clean（nothing to commit）
  - verify：绿。185 passed；用例/冒烟/红线三处 SKIP（卡 11/09/12-13 未实现，预期非回退）
  - 最近审核：card-03 = PASS（board/reviews/card-03.md，无 MUST_FIX，3 条非阻塞 RISK）
  - ⚠ 进程可信度发现：审核师审的版本是 183 passed / 119 条 DAO 测试 / dao.py 368 行，但最终提交 166f39d 是 185 passed / 121 条 / 371 行。差额 = 审核 PASS 后代码又被改：dao.py 的 `_insert` 加了 `volatile` 参数（幂等比对豁免自生成的 ts，3 行）+ 2 条测试（test_retry_across_a_second_boundary_is_still_idempotent / test_explicitly_different_ts_still_conflicts，共 37 行）。这个改动落在审核师重点审过的"幂等"语义上，且未经复核。

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均已 commit）
  - 卡在哪：card-03 代码与测试已提交，但存在"审核后增量未复核"的缝隙——审核结论与最终提交物不对版
  - 风险累积：新增"审核后改动未复核"第 1 次，但性质严重（改了幂等核心语义 + 审核结论失配），升级为阻塞：先 delta review 再推进

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: fix_first
REASON: card-03 存档点已落（166f39d），但审核 PASS 后代码又动了 3 行幂等逻辑 + 2 条测试才提交，审核结论覆盖的是旧版。这个增量恰在审核师重点审的幂等语义上，不能默认 PASS 依然成立。先让审核师对 166f39d 相对审核版的增量做 delta review，闭环后再跑 card-04（工具层首卡，含风控规则 + facts/markdown 幻觉校验，必须 v4-pro）。
NEXT_CARD_WARNING: 同上一卡：card-04 数字全部进 facts、generate_bill_report 数字与 facts 逐字一致、资源查询带 user 归属断言 + 越权单测（L2）、阈值常量注明来源；另注意 card-03 的 volatile 幂等豁免若被 delta review 判 FAIL，则 card-04 的写路径（虽然 T1-T5 是只读）也要等它闭环。

## 决策记录 2026-09-12 19:01

事实（自己查到的）：
  - 最新存档点：a3e25e7 board: card-03 记账（HEAD）
  - 工作区：有未提交改动 —— 仅 M board/ledger.md（本次记账）
  - verify：绿。185 passed（审核师 delta review 独立复跑 185 passed in 6.78s）
  - delta review 结论：card-03 审核后增量（volatile 幂等豁免 + 2 测试）= PASS，无 MUST_FIX。变异抽查 2 处（dao.py:342 改 volatile 为恒真、dao.py:180 删 name not in volatile）均 1 failed 真报警，已还原。3 条 RISK 均为非阻塞覆盖增强建议（见台账）。

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit，审核均闭环）
  - 卡在哪：数据层全部完成；工具层首卡 card-04（T1-T5 只读工具）尚未开始
  - 风险累积：上次阻塞（审核后改动未复核）已闭环归零；delta review 新出 3 条非阻塞建议传导卡 04；R1（transaction 不可嵌套）仍待卡 05；R3（单文件超行）第 1 次非阻塞

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: run
REASON: 阻塞已解除（delta review PASS、无 MUST_FIX），工作区只剩账本改动。card-04 是工具层首卡，含 detect_anomalies 风控规则 + generate_bill_report 的 facts/markdown 数字一致性（幻觉校验）+ user 归属断言（越权防护），必须 v4-pro。直接跑。
NEXT_CARD_WARNING: card-04 每个数字必须进 facts 包；generate_bill_report 的 markdown 数字与 facts 逐字一致；资源查询（get_card/get_subscription/get_product）带 user 归属断言 + 越权单测（L2）；阈值常量注明来源（近90天均值3倍 / 23:00-06:00 / 同商户1小时≥3笔）。

## 决策记录 2026-09-12 19:07

事实（自己查到的）：
  - 最新存档点：a3e25e7 board: card-03 记账（HEAD）；其下 166f39d card-03
  - 工作区：仅 M board/ledger.md（本次记账）；data/dao.py mtime 19:01 是审核师变异抽查 edit+revert 的残留，内容与 166f39d 逐字节一致（git status 无 data/ 改动）
  - verify：绿。185 passed（审核师 delta review 独立复跑 185 passed）
  - delta review：card-03 审核后增量（volatile 幂等豁免 + 2 测试）= PASS，无 MUST_FIX，变异抽查 2 处均真报警。过程责任已由 @worker 认领，无需回滚

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）
  - 卡在哪：数据层闭环；进入工具层前先做一次 DAO 硬化收尾（card-03b）
  - 风险累积：审核后改动未复核（R5）已闭环归零；300 行超限第 2 次出现 → 升级为阻塞；volatile 通用机制误用面 + retry 测试绑定时间来源，两条 delta RISK 一并进 card-03b 收口

NEXT_CARD: 03b
MODEL: deepseek-v4-pro
ACTION: human
REASON: 300 行超限已第 2 次出现（升级为阻塞）+ volatile 误用面是幂等静默失效隐患，收口这两条会改幂等比对语义、且是行为保持型重构，必须 v4-pro。card-03b 是张新卡，卡文件（docs/cards/card-03b.md）是人的地盘，需 @user 建卡后 worker 按正常流程做、审核师审。
NEXT_CARD_WARNING: （card-03b 范围已定，@user 照此建卡）① 拆 data/_dao_core.py 收 dao.py 41–206 行（常量 + connect_db/close/connection + _one/_many/_writing + 校验 helper + _insert/_apply_update），dao.py 只留 14 公开函数 + 反向 import 并 re-export connect_db/close/connection；依赖单向 dao→core，禁反向；两边 ≤300 行。② volatile 收窄为布尔 skip_ts（或断言 volatile⊆{"ts"}），语义不变，**不删 test_explicitly_different_ts_still_conflicts**。③ 假时钟 monkeypatch 目标 dao→data._dao_core 并断言补丁生效（防空补丁假绿）。④ tests/test_dao.py（425 行）拆成 test_dao.py + test_dao_core.py，不豁免。⑤ 补幂等用例：insert_txn 自生成 ts 连续两次幂等、ts=None 同 id 异 amount 必须 ValueError；口径 = ts 签名不动（必填位置参数）、ts=None 即自生成。⑥ 行为保持：185 测试原样绿，不改任何公开签名。


