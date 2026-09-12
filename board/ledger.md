# 团队进度账本（分析师维护，人也可以看）

> 格式：每张卡一段，由分析师追加。人也可以在这里写"人类批示"。

## 已接受的卡（存档点）

| 卡 | commit | 审核 | 备注 |
|---|---|---|---|
| card-00 | cd97242 | 人工 | 骨架 + 施工包 |
| card-00b | e307353 | 人工 | .hermes.md / docs/cards / run-card.sh |
| card-01 | e2c1046 | 未审（审核机制建于其后） | 数据层建库；我事后独立复验：pytest exit 0、schema 10 表与规格一致 |
| card-02 | 5561965 | PASS（board/reviews/card-02.md，无 MUST_FIX） | 12 个月合成数据；verify 绿。但 commit 被 `git add -A` 污染（见风险 R2） |

## 已知风险台账（同类风险出现 2 次即升级为阻塞）

| 首次出现 | 风险 | 影响 | 状态 |
|---|---|---|---|
| card-01 | `data/db.py` 的 `transaction()` 不可嵌套 | 卡 03/05 写多步事务时会抛 "cannot start a transaction within a transaction" | 待卡 05 验证 |
| card-02 | `git add -A` 把 board/.tmp/、agents/、scripts/agency.sh 等编排器脚手架混进卡 commit（5561965 共 12 文件，仅 2 个是卡 02 交付） | 提交历史污染；board/.tmp 提示快照已入 git 且每次运行都产生 diff，会滚雪球 | 升级为阻塞：卡 03 前先 .gitignore 挡 `board/.tmp/` + `git rm --cached` 已入库的 .tmp 文件 |

## 人类批示

- （空）

⚠ A previous `hermes update` pulled new code but did not restart running gateways.
  Gateways may still be serving pre-update modules (mixed sys.modules).
  Run `hermes update` or `hermes gateway restart`.
## 决策记录 2026-09-12 15:30

事实（自己查到的）：
  - 最新存档点：5561965 card-02: 生成 12 个月的合成银行数据
  - 工作区：有未提交改动，仅 `M board/.tmp/prompt-analyst.md`（这是被误提交后又被改的编排器临时快照，不是业务代码）
  - verify：绿。64 passed；用例/冒烟/红线三处 SKIP（卡 11/09/12-13 未实现，属预期，非回退）
  - 最近审核：card-02 = PASS（MUST_FIX 无）。但 RISKS-1 已兑现——`git show --name-status 5561965` 显示 commit 含 12 个文件，只有 data/seed.py + tests/test_seed.py 是卡 02 交付，其余 10 个（agents/*.md、board/.tmp/*.md、board/ledger.md、scripts/agency.sh、scripts/sync-soul.sh）是编排器脚手架，被 `git add -A` 一起提交。

进度判断：
  - 已完成卡：card-00、card-00b、card-01、card-02
  - 卡在哪：卡 02 审核 PASS 但 commit 被污染；下一张是 card-03（DAO 层）
  - 风险累积：R2（board/.tmp + 脚手架误提交）在审核里被预告、随后在 commit 里兑现，且 .gitignore 未挡 board/.tmp/，每次运行都会再产生 diff——属"已证明会复发"，升级为必须先在卡 03 前处理。transaction() 不可嵌套风险仍在，待卡 05 验证，不影响卡 03（DAO 单条插入不涉及嵌套事务）。

NEXT_CARD: 03
MODEL: deepseek-flash
ACTION: fix_first
REASON: 卡 02 代码与测试正确、verify 绿，但提交历史被 `git add -A` 污染且会复发。卡 03 前先做一条卫生修复：.gitignore 加 `board/.tmp/`，并 `git rm --cached` 已入库的 3 个 .tmp 快照（不改冻结接口、不重写历史，历史里那点 markdown 无害，重写风险更大）。修完再跑卡 03。
NEXT_CARD_WARNING: 提交时禁止 `git add -A`，只 `git add data/dao.py tests/test_dao.py`；DAO 不做权限/风控判断（那是 guard 的活）；insert_txn/insert_audit 是单条写入，若触碰多步事务会撞 card-01 的 transaction() 不可嵌套风险，别在卡 03 里强行嵌套。

已同步 ledger.md：card-02 入「已接受」表，新增风险 R2 并标记为阻塞。

session_id: 20260912_152853_24d19c
