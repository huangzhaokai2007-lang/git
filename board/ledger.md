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
| card-03 | （待提交） | PASS（board/reviews/card-03.md，无 MUST_FIX） | DAO 层 14 函数 + 119 测试；verify 绿（183 passed）。交付 data/dao.py + tests/test_dao.py 尚未 commit，存档点待补 |

## 已知风险台账（同类风险出现 2 次即升级为阻塞）

| 首次出现 | 风险 | 影响 | 状态 |
|---|---|---|---|
| card-01 | `data/db.py` 的 `transaction()` 不可嵌套 | 卡 05 写多步事务时会抛 "cannot start a transaction within a transaction" | 待卡 05 验证（不影响卡 03/04 单条读写） |
| card-02 | `git add -A` 把编排器脚手架混进卡 commit | 提交历史污染 | 已解决：5561965 已 reset 重做为 0c60abc；fb40cbb 已给 .gitignore 补 board/.tmp/，`git check-ignore` 验证生效 |
| card-03 | data/dao.py(371)/tests/test_dao.py(388) 单文件超 300 行，靠"顶层函数空 1 行"硬挤破坏 PEP8 | 评审逐条对代码规范可扣分 | 非阻塞（第 1 次）。建议后续卡抽 data/_dao_core.py；不阻塞卡 04 |
| card-03 | DAO 读接口（get_card/get_subscription/get_product）不按 user 圈定 | 属正确分层，但 tools/（卡 04-07）若不做资源归属断言则出现越权读他人卡/订阅 | 传导卡 04-07：每个资源查询带 user 归属断言 + 越权单测（规格 L2） |

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
