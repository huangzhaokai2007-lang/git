VERDICT: PASS
CARDS: card-23（+ 追认的 3 处越界）
REVIEWED_DIFF: 16 files changed, 527 insertions(+), 49 deletions(-)（`git show --stat ca2bf31`，我自己跑的）
  tools/card_query.py(新,67) tools/schemas.py(+42) agent/read_routes.py(新,39) agent/orchestrator.py(25) agent/templates.py(+52)
  data/_dao_core.py(+20) agent/classifier.py(2) README.md(9) docs/01(+5) docs/00(2) docs/02(14)
  tests/test_tools_card_query.py(新,261) tests/cases/orchestrator.yaml(16) tests/test_orchestrator_readonly.py(22) board/screenshots/card-23/*.png(2)

CHECKED:
  - 接口一致性：T18 `list_cards(status=None)` 逐条对齐规格 §2 —— data 恰 7 键
    `{card_id, card_no_mask, type, status, credit_limit, single_limit, daily_limit}` + `total_count`
    （= len(items)，无 limit 参数）；L0 只读；储蓄卡 `credit_limit=None`（不是 0）；枚举外 → `INVALID_ARGUMENT`。
    规格 T18 行文字与实现无出入，函数名/字段名未改名。
  - 越界改动（3 处，analyst 已追认，只查正确性，不按越界判罚）：`agent/classifier.py` card_query 槽位
    `("card_id",)→("status",)` 正确；`tests/cases/orchestrator.yaml` 30→31 条、card-001 断言翻转为
    `tool_calls:[list_cards]`、新增 card-005 带 `must_not_contain:["6222 **** **** 0001","lost"]`（有牙、方向正确）；
    `tests/test_orchestrator_readonly.py` 改名（read_only→unrouted）+ 2 断言
    （`set(TOOL_ROUTES)==set(READ_INTENTS)`、`all(name!="manage_card")`）语义正确 —— 均因 card_query 从
    「未接通」变「已接通」而必须改，不改则 verify 第 3 段必红（成立）。
  - 测试真实性（隔离复跑，非自证）：WT-M1 去掉 DAO 的 `user_id` 过滤 → 2 条归属用例红；
    WT-M2 静默忽略非法 status → `test_unrecognized_status_is_invalid_argument` 全部 10 条参数化红；
    WT-M4 `_item` 去掉掩码空格（凑成 16 位连续数字）→ `test_masks_are_copied_verbatim…` +
    `test_facts_carry_the_numbers_the_receipt_prints` 红；M3 改**其它**只读路由工具名
    (`analyze_spending`→`_v2`) → `tests/test_cases.py` 红（bill-001/bill-002 + 通过率）→ 证其它已接通意图被钉住；
    M5 空结果不独立成句 → `test_empty_result_has_its_own_sentence_not_a_broken_list_header` 等 2 条红。全部已还原。
  - 边界与异常：`frozen` 是合法状态但库内无生产者 → 回 `ok=True`/`total_count=0` 的空结果（不是错误）；
    空结果走**独立句子**模板（不套列表头）；status 校验双保险（工具层 `_invalid(ListCardsReq)` + DAO `_choice`）。
  - 权限/审计/安全（铁律 8 卡号）：**独立查库**（不是看截图）——`card` 表列 = `id,user_id,account_id,card_no_mask,
    type,credit_limit,single_limit,daily_limit,status`，**库里只有 `card_no_mask`、无完整卡号列**；全表 + `data`/`facts`
    dump + 捕获的 DEBUG 日志均**无 13+ 连续数字**；`card_no_mask` 照抄入库值（不拼接/不补全）。
    归属：DAO 把 `user_id` 当 **SQL 查询条件**（`WHERE user_id=?`），不是查完再过滤 → 他人的卡一张都出不来。

RISKS:
  1. **`.hermes.md`(第 14/36 行) 与 `CLAUDE.md`(第 27 行) 仍写「17 个工具/白名单函数」**，而 §2/§6/docs00/README/
     剧本已全为 18 —— 影响：新 agent 一进仓读项目规则会以为只 17 个，与规格冲突（卡 24 的 `test_spec_counts.py`
     只守 §2，管不到这两个文件）—— 建议：analyst 按卡 23 正文第 6 条（这两文件是受保护文件、由 analyst 另行落地）尽快闭环。
  2. **`tools/card_query.py:22` 直接 `from data._dao_core import list_cards`**（绕过公开门面 `data.dao`）：全仓其余
     14 个工具无一例外用 `from data import dao` —— 影响：`_dao_core` 是 data 层私有模块（设计约定 `dao → _dao_core`
     单向、禁反向），tools 直取私有模块是耦合口子，data 层重构需同步改这里 —— 建议：卡 24 给 data 层减负时
     把 `list_cards` 提到公开门面（或加一行薄 re-export）。
  3. **审与被审并行改工作区**：我 11:31 那一轮在**工作区**跑 verify 出现 20 failed（`ValueError: too many values to unpack`
     / pydantic 校验错，集中在写路径）—— 查实是 worker 的 card-24 在途改动（`M agent/write_flow.py`、
     `?? agent/write_intents.py`、`?? tests/test_spec_counts.py` …）落盘所致，**不是卡 23 的缺陷**。我已改用
     `git worktree` 隔离到 `ca2bf31` 复跑 → 1085 passed + verify 6/6 rc=0。影响：并行会污染账本/误判 ——
     建议：worker 提交前不要在他人复跑期间改工作区；复跑一律在工作区外的隔离副本上做。
  4. `agent/templates.py` 的 `render_cards` 有专测覆盖（含空结果独立句），但测试经编排层调用、不直呼函数名 ——
     仅可读性，非缺陷。
  5. `frozen` 状态保留但无生产者（T12 无该动作）；规格已注明「保留以对齐 schema」—— 若将来 T12 加 frozen 需同步。

MUST_FIX: 无

EVIDENCE:
  - `git show --stat ca2bf31` -> 16 files changed, 527 insertions(+), 49 deletions(-)
  - **隔离 worktree @ ca2bf31**（`git worktree add --detach … ca2bf31`，先验证 `tools.card_query.__file__` 指向 worktree）：
    `python -m pytest -q` -> **1085 passed**；`bash scripts/verify.sh` -> **rc=0、6/6 全绿**（段3 用例 **31/31**、段5 100 passed）
  - **真机（宿主，真 LLM + 真工具层）**：`uv run python -m app.cli "我几张卡"` -> `意图=card_query 工具=list_cards`
    3 张（信用卡·正常 额度 30,000.00 元 / 储蓄卡·正常 / 储蓄卡·已挂失）；
    `"我有没有挂失的卡"` -> 1 张（`6222 **** **** 0003` 储蓄卡·已挂失）；
    `"我的卡"` -> 3 张 —— 三种说法都命中 card_query，含已挂失那张
  - **铁律 8 独立查库**：`card` 表列名 + 全表 dump + `data`/`facts` dump + DEBUG 日志 -> 「有无 13+ 连续数字」全部 **False**
  - 变异抽查（WT = 在隔离 worktree 里复跑，防污染）：
      WT-M1 去掉 `user_id` 过滤 -> 2 failed；WT-M2 静默忽略非法 status -> 10 failed；
      WT-M4 卡号去空格拼接 -> 2 failed（并集 12 failed, 21 passed，已还原）
      M3 `read_routes` 改其它意图工具名 -> `test_cases.py` 3 failed；M5 空结果不独立成句 -> 2 failed（已还原）
  - 计数连锁核对：父提交（卡 20 后）§2 标题=17 但表内只有 T1–T16（**T17 行确缺**，worker 发现成立）；
    ca2bf31 标题 17→18 + 加 T18 行；`d9ddda2` 补 T17 行 -> 现 HEAD 标题=行数=18、README §5 表 18 行
  - 变异残留：`grep -rn "变异"` 主仓无我方残留；`git status` 只剩 worker 的 card-24 在途改动
  - 截图两图人工核：数字与库/我独立真机复跑逐字一致；仅掩码卡号；带 trace_id + 意图 + 状态机链 + 「合成数据」标注

VERDICT_REASON: 卡 23 的五条红线（卡号不外泄 / 归属 / status 口径 / 路由 / 计数连锁）逐条独立复跑+变异抽查通过，
隔离复跑 1085 passed + verify 6/6，真机三种说法都命中；两处 RISK（.hermes.md/CLAUDE.md 计数、_dao_core 直取）
均为 analyst 已授权延期或既有设计取舍，不构成本卡 MUST_FIX，故 PASS。
