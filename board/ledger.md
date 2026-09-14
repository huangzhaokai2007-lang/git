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
| card-04 | 745d960 | PASS（无 MUST_FIX，2 条非阻塞 RISK） | 工具层 T1–T5 只读工具；verify 绿（283 passed）。4 条红线变异抽查全真报警；3 文件指纹零漂移 |
| card-05 | 155804b | PASS（无 MUST_FIX，5 条 RISK，其中 3 条待 @user 口径） | 工具层 T6–T9 转账三段式（preview/execute 幂等 + AA 拆分，71 测试）；verify 绿（354 passed）。4 条变异抽查全真报警。前置 SPEC-CHANGE 0ff148d 删「备注」 |
| card-04b | 7d86b8d | PASS（无 MUST_FIX，4 条 RISK） | 工具层清理：共享 helpers 单份化 + 拆测试 ≤300 + DAO get_payee/update_account_balance + TOCTOU 锁；verify 绿（377 passed）。2 条变异抽查真报警、拆分零丢用例（旧 96→新 102） |

## 已知风险台账（同类风险出现 2 次即升级为阻塞）

| 首次出现 | 风险 | 影响 | 状态 |
|---|---|---|---|
| card-01 | `data/db.py` 的 `transaction()` 不可嵌套 | 卡 05 写多步事务时会抛 "cannot start a transaction within a transaction" | 待卡 05 验证（不影响卡 03/04 单条读写） |
| card-02 | `git add -A` 把编排器脚手架混进卡 commit | 提交历史污染 | 已解决：5561965 已 reset 重做为 0c60abc；fb40cbb 已给 .gitignore 补 board/.tmp/，`git check-ignore` 验证生效 |
| card-03 | data/dao.py(371)/tests/test_dao.py(388) 单文件超 300 行，靠"顶层函数空 1 行"硬挤破坏 PEP8 | 评审逐条对代码规范可扣分 | **降级为待办不阻塞**（用户 19:41 拍板折中：审核师本人未报此条、只出现 1 次，不构成第 2 次；留作后续卡抽 _dao_core.py） |
| card-03 | DAO 读接口（get_card/get_subscription/get_product）不按 user 圈定 | 属正确分层，但 tools/（卡 04-07）若不做资源归属断言则出现越权读他人卡/订阅 | 传导卡 04-07：每个资源查询带 user 归属断言 + 越权单测（规格 L2） |
| card-03 | 审核 PASS 后代码又被改（+volatile 幂等豁免 3 行 + 2 条测试）才提交 166f39d | 审核结论覆盖的是 183passed 版，最终提交是 185passed 版，差额未经审核师复核 | **已闭环**：审核师 delta review = PASS（无 MUST_FIX，变异抽查 2 处均真报警） |
| card-03（delta） | `test_retry_...` 里 txn 段用显式 STAMP 而非 None，insert_txn 自生成 ts 的豁免路径未被直接覆盖（仅 risk_event/audit 覆盖） | 若未来单独改坏 insert_txn 的 ts is None 分支，该测试不红 | 待办 DAO 补测：补 insert_txn(ACCOUNT, None, ...) 连续两次幂等用例（**不属卡 04 范围**，留作 DAO 补测小卡） |
| card-03（delta） | 「自生成 ts + 异内容 → 冲突」无直接用例 | 缺一条显式回归钉住该组合 | 待办 DAO 补测：加 ts=None、同 id、异 amount 必须 ValueError 用例（**不属卡 04 范围**） |
| card-03（delta） | 幂等比对用 `existing[name] != value` 直接比较，依赖列类型读回后与写入端一致 | 若将来扩表写入类型与 DDL 存储类型错位，会恒判「不同」误报冲突 | 待办：后续扩表时留意列类型一致性（非阻塞） |
| card-03（delta） | `_insert` 的 volatile 是通用列名元组，当前仅被 ts 用；未来误把业务列（amount）写进 volatile 会静默漏比对 | 同 id 异金额被当同一条吞掉（幂等静默失效） | **fix_first 先拆**：改成布尔 skip_ts（或断言 volatile ⊆ {"ts"}），行为保持 185 测试原样绿 |
| card-03（delta） | retry 测试 `monkeypatch.setattr(dao, "datetime", Clock)` 绑定「DAO 用模块级 now() 取时间」的实现细节 | 改时间来源（sqlite CURRENT_TIMESTAMP / time.time()）后该测试假绿/假红 | 传导：将来动时间来源时同步改该测试 |
| card-04 | 规格 §5 `amount_jump(>历史均值5倍)` 与卡 04「金额偏离 >近90天均值3倍」数字打架 | 评审/答辩时口径不一致被扣分 | 已闭环（SPEC-CHANGE e4e847a）：标注 §5=写操作降级因子、T4=只读检测阈值，场景不同非冲突 |
| card-04 | 规格 T4 备注列「陌生商户」但卡 04 未给口径 | T4 只做 3 条规则、少 1 条，评审对照规格会发现缺项 | 待办：与 §5 new_payee 语义重叠，后续卡统一口径后补（SPEC-CHANGE 已标注待定） |
| card-04 | query.py(497)/test_tools_query.py(651) 单文件超 300 行 | 评审对代码规范可扣分（同类风险第 2 次，但纯风格） | 待办不阻塞：建议 04b 抽 tools/_query_common.py + 拆测试文件（范围只许改 3 文件，本卡拆不了） |
| card-04 | 2 处注释/docstring 过时：query.py:32 来源写「§5 amount_jump」应改「§2 T4 备注」；schemas.py:24 还写「... 未封闭、待人类决定」现已入册 | 零行为影响，但审核师查「来源标注与规格一致」会碰到 | 待修（审核闭环后一口价修，审核期间不动以免结论失配）；已同步给 worker |
| card-04 | 交付版本指纹（防审核后改动失配） | — | schemas.py `3350d7c9…` / query.py `e72fa8a0…` / test_tools_query.py `bc214f99…`；行数 142/497/678，我实测 sha256 逐一匹配 |
| card-04 | 审核 RISK-1：facts 归一化弱于「逐字一致」（numbers() 剥小数点/千分位），挡不住量级错 | markdown 机械生成无硬编码，残余风险低，但卡 13 数字校验器若复用此归一化会漏量级错误 | 传导卡 13 前：补逐字断言（卡 13 做 facts_check 时收口） |
| card-04 | 审核 RISK-2：T2 fail-closed 时 total_count 会小于真实值（他人账户 id 更小遮蔽本人） | 单用户 demo 不触发；多用户场景下 total_count 偏低 | 待办：多用户/越权场景扩展时复核 total_count 口径 |
| card-05 | T6 要求「按备注匹配」但 payee 表无 memo 列（规格自相矛盾） | resolve_payee 无法落「备注」，且 memo 是不可信文本（txn 表） | **已落地**：SPEC-CHANGE 0ff148d 删规格 §2 T6 + 剧本源 + card-05.md 重生成（3 files，逐字一致自查） |
| card-04 | 账本记「审核 PASS」但 board/reviews/ 无 card-04.md（零日志零 prompt 零 review 文件） | 11 月评审翻裁决时 card-04 空、PASS 无可核对实物 | **已补记**：reviewer 复验 745d960（96 passed + 4 条红线变异全真报警）→ 写 board/reviews/card-04.md 标「事后复验补记」 |
| card-05 | 并发幂等 TOCTOU：execute 的 `token["state"]=="executed"` 检查在事务外、`_TOKENS` 模块级 dict，两线程同时 execute 同 token 会双重扣款 | 单线程 demo（Streamlit）不触发；11 月评审若上线程压测会翻车 | **待 @user 口径**：并发=顺序重复（单线程）或加 threading.Lock + state 翻转挪进事务 |
| card-05 | `fee` 恒为 0：规格 §2 T7 要求返 fee 但未定义费率，worker 未编造费率（正确） | 评审对照规格会问「fee 字段恒 0 的意义」 | **待 @user**：给手续费费率口径（或确认 demo 期 fee=0） |
| card-05 | `_payee_by_id`/`_debit` 直查 `dao.connection()` 绕过 DAO 原语（TODO dao-05b） | ⑤ 层绕过 DAO 原语，层级味道；但参数化查询无注入、卡范围只许改 2 文件 | 建议 05b 补 DAO `get_payee(id)` + `update_account_balance` |
| card-05 | 规格 §5 自相矛盾：`new_payee` 既在 L2 基础条件、又在降级因子清单 | 非白名单收款人可能永远到不了 L2+OTP（被误判 L3） | **待 @user**：确认「基础档已体现的因子不再升档」折中口径，或修规格 §5 |
| card-05 | `_money` 硬编码 `100`（query.py 用 `PCT_TOTAL`） | 跨模块微小不一致 | 非阻塞：跨模块一致测试 `test_money_and_ownership_helpers_agree_with_query_module` 钉住 |
| card-05 | transfer.py(480)/test_tools_transfer.py(604) 单文件超 300 行 | 300 行超限**第 3 次**（card-03 dao / card-04 query / card-05 transfer），且 transfer/query 薄封装重复（_ok/_fail/_invalid/_money/_owned_account_ids） | **部分收口**：04b 已拆测试文件 ≤300 + 抽共享；但**源文件仍超 300**（见 card-04b RISK-1），待 05b 拆源文件 |
| card-04b | 源文件 300 行没治本：query.py 418 / transfer.py 452 / dao.py 393 / test_dao.py 525 仍超（抽共享只降 30–80 行） | card-06 是 L2/L3 高危写，带 4 个超限文件进场，评审逐条对规范可扣分 | **待 05b**：拆 tools/_query_analysis.py + tools/_transfer_risk.py + data/_dao_core.py + 再拆 test_dao.py |
| card-04b | 真并发用例靠 monkeypatch `data.db.connect`（生产连接 check_same_thread=True 默认，进程内单连接） | TOCTOU 锁本身已变异证真，但 demo 若真多线程，第二个线程会 ProgrammingError | **待 @user**：demo 是否多线程；是则 data/db.py 开 check_same_thread=False |
| card-04b | `VELOCITY_WINDOW_MINUTES` 同名不同义：query.py=60（T4 只读）/ transfer.py=10（§5 写降级），未合并（正确） | 同义不同名易误改其一 | 建议 05b 拆两个名字（如 VELOCITY_MINUTES_T4 / VELOCITY_MINUTES_WRITE） |
| card-04b | 5 个新文件超卡明文范围（conftest.py + 4 个拆分测试文件） | 是「678/604 行拆 ≤300」的必然结果，非越界 | 待 @user 追认（避免下张卡被范围门禁卡住） |

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

## 决策记录 2026-09-12 19:41

事实（自己查到的）：
  - 最新存档点：76853b2 board: card-03 delta review 闭环 + 决策 card-03b（HEAD）
  - 工作区：仅 M board/ledger.md（本次记账）
  - verify：绿。185 passed
  - 审核师 delta review 二次确认 = PASS，无 MUST_FIX，明确放行 card-04；其 RISK 清单只有 3 条覆盖建议，**不含「单文件超 300 行」**
  - 人类决策（用户拍板）：折中方案——只做 volatile 收窄，单文件超 300 行抽 _dao_core.py 降级为待办不阻塞

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）
  - 卡在哪：进工具层前先做一次 fix_first（volatile 收窄），做完即进 card-04
  - 风险累积：19:07 的「单文件超 300 行第 2 次→阻塞」判定被推翻——审核师本人未报此条、只出现 1 次，降级为待办；volatile 通用机制误用面（幂等静默失效）保留为 fix_first 先拆

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: fix_first
REASON: 用户拍板折中——先拆 volatile 误用面这颗地雷（改布尔 skip_ts，改幂等比对语义、行为保持型重构，必须 v4-pro），「单文件超 300 行抽 _dao_core.py」纯风格问题降级为待办不阻塞。fix_first 完成后让审核师 delta review，再跑 card-04。
NEXT_CARD_WARNING: （fix_first 范围，勿越界）只改 data/dao.py 的 _insert volatile 收窄为布尔 skip_ts + 补 2 条幂等覆盖用例（insert_txn 自生成 ts 连续两次、ts=None 同 id 异 amount 冲突）；**185 测试必须原样绿 + 新增用例绿**；不抽 _dao_core.py、不动 PEP8（那些降级待办了）。

## 决策记录 2026-09-12 19:46

事实（自己查到的）：
  - 最新存档点：76853b2（HEAD）
  - 工作区：M board/ledger.md（账本）+ M data/dao.py + M tests/test_dao.py（fix_first 交付，未提交）
  - verify：绿。187 passed（185 原样绿 + 新增 2）；用例/冒烟/红线三处 SKIP（预期）
  - 我独立复核 diff：dao.py 的 `_insert` 参数 `volatile: tuple[str,...]` → `skip_ts: bool`，比对条件 `if not (skip_ts and name == "ts")`，3 个调用点改 `skip_ts=ts is None`；公开签名零改动。test_dao.py 新增 ticking_clock fixture + 2 条用例（自生成 ts 幂等 / 自生成 ts 不豁免 amount），假时钟前进 1 秒 + 末尾断言补丁生效，防空补丁假绿。

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）
  - 卡在哪：fix_first（volatile 收窄）代码与测试已交付、verify 绿，待审核师 delta review；闭环后开 card-04
  - 风险累积：volatile 误用面已由 fix_first 拆除（待审）；300 行超限仍降级待办（dao.py 374 / test_dao.py 465，比审核时又长 40 行，属预期，留作后续卡）

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: fix_first
REASON: fix_first 交付已 self-verify 绿、diff 我复核干净，但改的是幂等比对语义，按流程必须过审核师 delta review 才能提交并开 card-04。先派审核师，闭环后精准提交（只 add dao.py/test_dao.py，禁 -A）。
NEXT_CARD_WARNING: 提交只 `git add data/dao.py tests/test_dao.py`（fix_first 提交）；board/ledger.md 单独记账提交。card-04（工具层首卡）注意事项同前：数字全进 facts、generate_bill_report 数字与 facts 逐字一致、资源查询带 user 归属断言 + 越权单测（L2）、阈值常量注明来源。

## 决策记录 2026-09-12 19:49

事实（自己查到的）：
  - 最新存档点：becf373 board: card-03b 范围定稿（HEAD）
  - 工作区：M board/ledger.md + M data/dao.py + M tests/test_dao.py（fix_first 未提交）
  - verify：绿。187 passed（185 原样绿 + 2 新增）
  - 审核师 delta review = PASS，无 MUST_FIX。变异抽查 2 处真报警（skip_ts 改 False → 用例1红；豁免集加 amount → 用例2红）；防假绿验证通过（假时钟前进 1 秒 + 末尾断言时钟在走）。3 条非阻塞 RISK（假时钟样板重复 / "ts" 字符串锚 / 金额守卫只覆盖 txn 表）。

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）；fix_first（volatile 收窄）delta review PASS
  - 卡在哪：fix_first 闭环，精准提交后开 card-04
  - 风险累积：volatile 误用面已拆除；300 行超限降级待办（dao.py 374 / test_dao.py 465）；审核师新出 3 条非阻塞 RISK 传导后续

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: run
REASON: fix_first delta review PASS、无 MUST_FIX，审核师明确放行精准提交。精准提交 dao.py + test_dao.py 后开 card-04（工具层首卡，风控规则 + facts/markdown 幻觉校验 + 越权防护，v4-pro）。
NEXT_CARD_WARNING: card-04 数字全进 facts、generate_bill_report 数字与 facts 逐字一致、资源查询带 user 归属断言 + 越权单测（L2）、阈值常量注明来源；审核师 3 条 RISK（假时钟样板抽共享 fixture、"ts" 列名勿偏离 DDL、新金额列表复刻守卫用例）留作后续顺手补。

## 决策记录 2026-09-12 20:05

事实（自己查到的）：
  - 最新存档点：e4e847a SPEC-CHANGE: card-04 口径补全（HEAD）；其下 94de153 board 记账 / ac11268 fix_first
  - 工作区：?? tools/query.py、tools/schemas.py、tests/test_tools_query.py（card-04 交付，未提交）
  - verify：绿。283 passed（187 原有 + 96 新增工具层测试，实测 283、worker 报 282，以实测为准）；用例/冒烟/红线三处 SKIP（预期）
  - worker 报 DONE_WITH_CAVEAT，抛 5 条口径问题；我逐条对照规格原文核实，4 条属实（error_code 未封闭 / 当前 user 未定义 / 3倍vs5倍打架 / 陌生商户缺口径），1 条纯风格（300 行超限）
  - 人类决策（用户拍板）：5 条全部按我的建议批准 → 已改规格并提交 SPEC-CHANGE e4e847a

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）；card-04 代码+测试已交付、verify 绿，规格口径已由人拍板补齐
  - 卡在哪：card-04 待审核（代码未提交，等审核师审完再精准提交）
  - 风险累积：3倍vs5倍已闭环（SPEC-CHANGE 标注）；陌生商户待办（后续卡补）；300行超限待办（04b 拆）；error_code/当前user 已入册认可

NEXT_CARD: 04
MODEL: deepseek-v4-pro
ACTION: run
REASON: 规格口径已由人拍板并提交 SPEC-CHANGE（e4e847a），worker 的 5 条 CAVEAT 全部有了落点（3 条入册/认可、2 条待办）。代码 verify 绿、范围干净，可以派审核师审 card-04，审完精准提交。
NEXT_CARD_WARNING: 审核师会重点看 facts/markdown 数字一致性 + L2 越权断言 + 阈值常量来源；card-04 代码未提交，审核通过后只 `git add tools/query.py tools/schemas.py tests/test_tools_query.py` 精准提交（禁 -A）。陌生商户、300行超限为待办，本卡不要求补。

## 决策记录 2026-09-12 20:41

事实（自己查到的）：
  - 最新存档点：bb4ae39 board: card-04 口径拍板（HEAD）；其下 e4e847a SPEC-CHANGE
  - 工作区：?? tools/query.py、tools/schemas.py、tests/test_tools_query.py（card-04 交付，未提交）+ M board/ledger.md（记账）
  - verify：绿。283 passed；用例/冒烟/红线三处 SKIP（预期）
  - 审核师 card-04 = PASS，无 MUST_FIX。四条红线变异抽查全真报警（facts 塞 9999 / require_owned 置空 / _owned_account_ids 放行他人 / _vs_prev_pct 改 float）。3 文件指纹 e72fa8a0 / bc214f99 / 3350d7c9 与我实测一致，零漂移。
  - 审核师 2 条非阻塞 RISK：① facts 归一化弱于逐字一致（numbers() 剥小数点/千分位），挡不住量级错，卡13前补逐字断言；② T2 fail-closed 时 total_count 会小于真实值（他人账户 id 更小遮蔽本人），单用户 demo 不触发。

进度判断：
  - 已完成卡：card-00/00b/01/02/03（均 commit + 审核闭环）；card-04 = PASS，待精准提交
  - 卡在哪：card-04 审核 PASS，精准提交后进工具层第二卡 card-05
  - 风险累积：新增 2 条审核 RISK（facts 逐字断言待补、T2 total_count 遮蔽）均为非阻塞；陌生商户/300行超限/2处注释仍待办

NEXT_CARD: 05
MODEL: deepseek-v4-pro
ACTION: run
REASON: card-04 审核 PASS、无 MUST_FIX、零漂移，先精准提交（只 add tools/ 3 文件，禁 -A）+ 记账。下一张 card-05（转账工具 T6–T9）涉及写操作 + 幂等 + 权限档 + 多步事务（会撞 card-01 的 transaction 不可嵌套风险），必须 v4-pro。
NEXT_CARD_WARNING: card-05 提交只 `git add tools/transfer.py tests/test_tools_transfer.py`；写操作必须走 preview→权限档→确认→幂等执行，且写 audit_log；多步事务注意 card-01 的 transaction() 不可嵌套（别在卡 05 强行嵌套）。2 处注释待修（query.py:32、schemas.py:24）留待审核闭环后一口价，不阻塞卡 05。

## 决策记录 2026-09-14 13:04

事实（自己查到的）：
  - 最新存档点：21f8344 docs 注释修正（HEAD）；其下 633da2d SPEC-CHANGE(c05562c 代码侧落地 ErrorCode +3) / f950723 board 补正 / c05562c SPEC-CHANGE
  - 工作区：仅 ?? tools/transfer.py(479) + ?? tests/test_tools_transfer.py(604)（card-05 交付，未提交）；schemas.py/query.py 的改动已由 633da2d + 21f8344 提交干净
  - verify：绿。354 passed（187 + 96 工具层 + 71 转账）
  - (A) 顺序第 1、2 步已落地：633da2d（ErrorCode 补 3 码，前置 card-05）+ 21f8344（query.py 注释修正，纯注释零行为）
  - 备注状态：transfer.py:278 已按 name/phone/bank 匹配、注释明写「payee 无备注列，待确认」；但规格 T6（第 94 行）与 card-05.md 要求 #1 仍写「备注」待删
  - card-04 缺口仍在：board/reviews/ 只有 card-02/card-03，无 card-04.md

人类决策（用户拍板，2026-09-14）：
  - ①「备注」删（我 + worker + reviewer 三人一致建议）：T6 只按 name/phone 匹配
  - ② 批 card-04 事后复验补记：reviewer 亲自复验 745d960 + 写 board/reviews/card-04.md 标「事后复验补记」
  - ③ 单进程化：每角色只留一个进程（本会话 @analyst 即唯一 analyst）

进度判断：
  - 已完成卡：card-00/00b/01/02/03/04（均 commit + 审核闭环；card-04 待补落盘裁决物）
  - 卡在哪：card-05 代码就绪（verify 绿），差 3 件收尾：备注 doc 删（SPEC-CHANGE）、card-04 补记、单进程
  - 风险累积：备注冲突（规格自相矛盾）已拍板删；card-04 无落盘裁决物已拍板补记；并行进程已拍板单进程化

NEXT_CARD: 05
MODEL: deepseek-v4-pro
ACTION: run
REASON: 三件拍板齐了，card-05 代码已交付且 verify 绿。执行顺序：(1) SPEC-CHANGE 删「备注」（规格 T6 第 94 行 + docs/02-AI指令剧本.md 源 + 重生成 card-05.md）→ (2) reviewer 补记 card-04 → (3) 提交 card-05（只 add tools/transfer.py tests/test_tools_transfer.py）→ reviewer 审 card-05。写操作 + 幂等 + 权限档 + 多步事务，必须 v4-pro。
NEXT_CARD_WARNING: card-05 提交只 `git add tools/transfer.py tests/test_tools_transfer.py`；resolve_payee 只按 name/phone 匹配（备注已删，勿再碰）；execute_transfer 幂等只扣一次款、金额纯整数分、多步事务不嵌套（card-01 的 transaction 坑）、TOKEN_EXPIRED 走 ErrorCode 枚举不硬编码；reviewer 重点核这四条 + T6 越权断言。

## 决策记录 2026-09-14 13:22

事实（自己查到的）：
  - 最新存档点：155804b card-05（HEAD）；其下 0ff148d SPEC-CHANGE 删备注 / 1a082ba board 拍板 / 21f8344 docs / 633da2d SPEC-CHANGE
  - 工作区：仅 ?? board/reviews/card-04.md（reviewer 补记，待本次记账提交）
  - verify：绿。354 passed（187 + 96 工具层 + 71 转账）
  - card-05 = PASS（无 MUST_FIX，5 条 RISK，其中 3 条待 @user 口径）；4 条变异抽查全真报警
  - 备注已删（0ff148d 三处：规格 §2 T6 + 剧本源 + card-05.md 重生成，逐字一致自查通过）
  - card-04 补记已落盘（board/reviews/card-04.md，VERDICT PASS，事后复验补记，96 passed + 4 条红线变异全真报警）

进度判断：
  - 已完成卡：card-00/00b/01/02/03/04/05（均 commit + 审核闭环）
  - 卡在哪：工具层 04/05 完成；下一张 card-06（T10-T12 订阅/卡）；card-04 补记文件随本次记账一并提交
  - 风险累积：3 条新 @user 口径（TOCTOU 并发 / fee 费率 / new_payee 规格矛盾）非阻塞但挂 11 月评审；300 行超限第 3 次（dao/query/transfer）→ 升级信号，建议 04b/05b 抽 tools/_query_common.py 收口

NEXT_CARD: 06
MODEL: deepseek-v4-pro
ACTION: run
REASON: card-05 PASS 无 MUST_FIX，工具层剩 card-06（T10-T12 订阅/卡）。订阅取消/卡挂失属 L2/L3 高危写操作 + confirm_ref 确认卡闭环 + 越权 + 写审计，必须 v4-pro。3 条 @user 口径（TOCTOU/fee/new_payee）不阻塞 card-06（订阅/卡与转账无耦合），边做边等拍板。
NEXT_CARD_WARNING: card-06 高危写操作（cancel_subscription/manage_card）走 confirm_ref 确认卡闭环、越权断言、写 audit_log；金额整数分；L2/L3 权限档按规格 §5 矩阵；提交只 add 卡范围文件禁 -A。顺手留意 300 行（新文件尽量复用 helpers 或拆小，别让第 4 次超限滚雪球）。

## 决策记录 2026-09-14 13:43

事实（自己查到的）：
  - 最新存档点：6cceba3 board: card-05 记账（HEAD）；其下 155804b card-05 / 0ff148d SPEC-CHANGE 删备注
  - 工作区：clean；verify 绿 354 passed
  - 上轮 4 项待拍（TOCTOU / fee / new_payee / 300 行清理）——用户拍板：按 reviewer 建议批准

人类决策（用户拍板，2026-09-14）：
  - ① 04b 清理卡先做、再进 card-06（reviewer 翻转成功：高危写路径上第 4 份 helper 副本 = 正确性风险，非风格债）
  - ② TOCTOU → 04b 修（threading.Lock + state 翻转挪进事务）
  - ③ fee → demo 期 fee=0 写进规格备注（不编费率，避免 float 撞禁浮点金额）
  - ④ new_payee → 修 §5 去重（降级因子清单删 new_payee，保留 L2 基础条件）

进度判断：
  - 已完成卡：card-00/00b/01/02/03/04/05（均 commit + 审核闭环）
  - 卡在哪：工具层 04/05 完成；先 04b 清理（抽 _query_common + 拆测试 + DAO get_payee/update_account_balance + TOCTOU 锁），再 card-06
  - 风险累积：300 行超限（第 3 次）由 04b 收口；TOCTOU/fee/new_payee 三条口径已拍板待落地

NEXT_CARD: 04b
MODEL: deepseek-v4-pro
ACTION: human
REASON: 用户批准 04b 清理先做。需 @user 建 docs/cards/card-04b.md（范围见下）+ 2 处 SPEC-CHANGE（fee=0 备注、new_payee §5 去重）落地。行为保持重构 + 幂等锁 + DAO 原语，必须 v4-pro。
NEXT_CARD_WARNING: （04b 范围）tools/_query_common.py（新）抽 _ok/_fail/_invalid/_money/_owned_account_ids/require_owned/PCT_TOTAL；query.py/transfer.py 改引用共享删本地副本；拆 test_tools_query.py(651)/test_tools_transfer.py(604)；data/dao.py 补 get_payee(id)+update_account_balance（替 transfer.py 直查）；transfer.py _TOKENS 加 threading.Lock + state 翻转挪进事务；行为保持 354 测试原样绿。另 2 处 SPEC-CHANGE：规格 fee 备注「demo 期 0」+ §5 降级因子删 new_payee。

## 决策记录 2026-09-14 14:05

事实（自己查到的）：
  - 最新存档点：7d86b8d card-04b（HEAD）；其下 f5f24d7 card-04b.md / bd8fa64 + 61711f7 SPEC-CHANGE
  - 工作区：clean；verify 绿 377 passed（354 原样 + 23 新增）
  - card-04b = PASS（无 MUST_FIX，4 条 RISK）；2 条变异抽查真报警；拆分零丢用例（旧 96 → 新 102，+6）
  - 13 文件精准提交（6 改 + 7 新，未用 -A）

进度判断：
  - 已完成卡：card-00/00b/01/02/03/04/05/04b（均 commit + 审核闭环）
  - 卡在哪：04b 收口了测试拆分 + 共享 helpers + DAO 原语 + TOCTOU；但源文件 300 行未治本（query 418/transfer 452/dao 393/test_dao 525），待 05b 拆源文件后再进 card-06
  - 风险累积：300 行超限第 4 次（dao/query/transfer/test_dao）→ 05b 必须收口；3 条 RISK 传导（并发连接 check_same_thread / 同义不同名常量 / 5 新文件追认）

NEXT_CARD: 05b
MODEL: deepseek-v4-pro
ACTION: run
REASON: card-04b PASS 无 MUST_FIX，但源文件 300 行只治了测试没治本体（query 418 / transfer 452 / dao 393 / test_dao 525）。card-06 是 L2/L3 高危写，不能带着 4 个超限文件 + 同义不同名常量进场。05b 拆 tools/_query_analysis.py + tools/_transfer_risk.py + data/_dao_core.py + 再拆 test_dao.py，一次收口后再 card-06。行为保持重构，v4-pro。
NEXT_CARD_WARNING: （05b 范围）拆 3 个源文件 + 再拆 test_dao.py ≤300；VELOCITY_WINDOW_MINUTES 拆两个名字（T4 只读 60 vs §5 写降级 10）；行为保持 377 测试原样绿。3 条 @user 口径待拍：① 05b 做不做（我建议做）② demo 是否多线程（决定 data/db.py 要不要 check_same_thread=False）③ 5 个新文件追认。






