# F20 送礼计划行为设计

F20 验收清单第 3 条原文要求「重复提交不重复锁款，**失败回滚及取消/释放边界需先明确**」（`docs/features/F20-gift-plan.md`）。本文定这几件行为与契约影响，**不含代码实现**。

## 背景：为什么先定方案不写代码

工具层 T16 `plan_gift(contact, date, budget)` 已实现（`tools/cross_scene.py:132`），但接通编排层卡在三处**契约级问题**上，每一处都要人类拍板才能动：

1. **锁资金无表无解冻**：`tools/cross_scene.py:10-11` 自述「**DDL 没有锁资金表**（也没有放款/解冻工具，规格未定）→ 本层用 `available` 扣减顶住」，`:89` 挂着 `TODO(07b)`。现实现 `_lock_funds`（`:86-101`）直查直改 `account.available`，绕过 DAO 原语层。
2. **确认卡取数无来源**：T16 **没有 `preview_*`**，而确认卡上的每个数字都必须能在事实包里找到（铁律 2 + `agent/write_flow.py` 的 `card_numbers_outside_facts` 守门）。想按 §6 交互层要求「复述我理解的意图 + **金额** + 收款人」显示预算，就必须有只读取数入口。
3. **写路径签名装不下参数**：`agent/write_flow.py:123` 调的是 `spec.execute(confirmation.target_id, confirmation.credential, otp)` —— 只有**单个主键**，没有 `date` 也没有 `budget`；而 `Confirmation`（`agent/confirm_card.py`）的字段里**没有能放 `date` 的地方**。

## 确认前不锁资金：靠描述符的 prepare/execute 分界

`agent/write_intents.py` 的描述符把「只算不执行」与「真执行」分成两个函数（`prepare` 造确认卡事实包、`execute` 才落库写），这正是铁律 3 第一步的落点。故：

- `prepare` **不调用 `plan_gift`**（它会锁资金）→ 满足 F20 验收第 1 条「确认与 OTP 前不锁定资金、不生成模拟预订」；
- `execute` 才调 `plan_gift` → 锁资金 + mock 预订 + 写审计，全部在用户确认之后。

日期口径沿用工具层 `_wanted_date`（`tools/cross_scene.py:106`）：ISO8601 且不早于 `data.seed.AS_OF`（= 2026-09-12），保证跨天可复现、测试可钉死。

## 幂等：靠确认卡一次性消费，如实登记剩余限制

T16 签名没有 `preview_token`，因此**没有工具层幂等落库**（不同于 T8 的 `idempotency` 表）。本期的重复提交保护来自编排层：

- `agent/write_flow.py:131` 执行成功后 `confirm_card.clear(session_id)` → 同一会话的第二次「确认」找不到在途确认，会**重新分类**而不是再锁一次款。

**缺口如实登记**：若绕过编排层直接调 `plan_gift`，或会话态丢失（进程重启），**没有第二道防线**。这与 T9（AA 收款）、T11 族以外的无 token 工具同属一类，建议随 Q2 的签名改造一并评估。

## 失败回滚：已满足，无需改动

`plan_gift` 用单一事务包住「锁资金 + 写审计」（`tools/cross_scene.py:146-147`，`with transaction(conn)`），任一失败整笔回滚；`INSUFFICIENT_FUNDS`（`:96-97`）在锁之前就返回，不会留下半锁状态。越权时另走 `reject_audit` 留痕（`:156-157`）。

## 取消 / 释放：本期不做，记为保证性缺口

**没有解冻原语**，所以「取消这笔送礼计划并释放已锁资金」在当前代码里**做不到** —— 锁一旦落下，`available` 就被永久扣减。

补齐它需要两件东西，都属规格冻结范围：

- `data/schema.sql` 加**锁资金表**（现 12 张表里没有；`data/schema.sql` 全文核对过）；
- DAO 补 `lock_funds / unlock_funds` 原语，替掉 `_lock_funds` 的直查直改。

**建议本期不做**：W4（10-19～10-25）在排期里只剩一格，而这是一整个独立施工项。如实登记为剩余限制，另开一卡（即 `TODO(07b)`）。

## 契约影响：两处 `SPEC-CHANGE` 候选（待审批）

### 候选一：新增 T19 `preview_gift`（建议采纳）

```
T19  preview_gift(contact, date, budget)
     入参：与 T16 同形
     data：items[{item_id, name, price}]、total、available、balance
     权限档：L0（只读，**不锁资金、不写审计**）
```

论证与 F18 的设计（`docs/superpowers/specs/2026-10-03-f18-subscription-reminder-design.md` Q4 条）**完全同构**：确认卡上的数字必须有工具层来源（铁律 2），把「按预算挑礼物」这个业务口径落在编排层会让同一份选品逻辑出现两份实现，日后必然漂移。

- 流程：人类改 `docs/01-接口规格.md`（§2 标题 19 或 20 → 补 T19 行）→ commit 注明 `SPEC-CHANGE` → 通知全员 → 才可写代码。**`tests/test_spec_counts.py` 有机器守卫盯着标题与表行数一致，改一处必须同时改另一处。**
- ⚠️ **与 F18 的 T19 冲突**：F18 设计同样申请 `remind_subscription` 为 T19。两者都落地时需编号顺延（T19/T20），由队长一次性裁决，避免两份设计各写各的。

### 候选二：写路径 `execute` 签名（建议采纳，且能一并解 F15）

```python
# 现状（agent/write_intents.py:90）
execute: Callable[[str, str, str | None], ToolResult]      # (target_id, credential, otp)
```

`target_id` 只装得下单主键，装不下 `gift_plan` 的 `(contact, date, budget)`，也装不下 **F15 AA 收款**的 `create_aa_request(payee_ids: list, amount)`。

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（建议）** | `Confirmation` 加一个槽位快照字段（如 `filled: dict = {}`），`_execute` 改传 `confirmation.filled` | 一次解决 F20 与 F15；`Confirmation` 属**编排层契约、非冻结工具契约**，加字段合法；3 个既有 `execute` 实现只改取值方式，**行为不变** |
| B | 把多参数编码进 `target_id` 字符串 | 脏，审核师会挑 |
| C | 每个意图自带一个 `preview_*` 工具顺便当数据载体 | 数量翻倍，且 T9/T16 都已冻结 |

**方案 A 动的是共享写路径**（`agent/write_intents.py` + `agent/write_flow.py` + `agent/confirm_card.py`）。按 `CONTRIBUTING.md`，改共享编排文件前须联系**本周集成人员**；W3 的集成人员是**汤**。建议与 F15 合并成一次改造，避免同一文件被改两遍。

## 实现路径与已知约束（审批通过后执行）

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1 | `agent/confirm_card.py` | `Confirmation` 加快照字段（方案 A） |
| 2 | `agent/write_flow.py` | 构造 `Confirmation` 时带上快照；`_execute` 改传快照 |
| 3 | `agent/write_intents.py` | 加 `gift_plan` 描述符 + 五个纯函数；既有 3 条 `execute` 改签名。**注意本文件现 295 行，距 300 行红线只剩 5 行** —— 需先把描述符表拆出，或压缩注释 |
| 4 | `tools/cross_scene.py` | 加 T19 `preview_gift`（若候选一获批） |
| 5 | `docs/01-接口规格.md` | 人类更新（§2 标题 + T19 行） |
| 6 | `tests/cases/orchestrator.yaml` | 加 `gft-*` 用例（预算不足、日期早于基准日、超可用余额、L2 需 OTP） |
| 7 | `tests/` | 补确认前不锁资金（断言 `available` 不变）、幂等、失败回滚、用户隔离 |

## 待拍板清单

| # | 问题 | 建议 |
|---|---|---|
| Q1 | 确认卡要不要显示预算与礼物清单 | 要；按铁律 2 必须有工具层来源，故采纳候选一 |
| Q2 | 新增 T19 `preview_gift`，还是编排层自行算 | 新增 T19（与 F18 同构论证） |
| Q3 | T19 与 F18 的 T19 编号冲突怎么解 | 队长一次性裁决，顺延为 T19/T20 |
| Q4 | 写路径 `execute` 签名改造（方案 A/B/C） | 方案 A，且与 F15 合并成一次改造 |
| Q5 | 取消/释放资金本期做不做 | 不做，登记为剩余限制，另开 07b 卡 |
| Q6 | `GIFT_CATALOG` 价目无规格来源（鲜花 199 / 蛋糕 299） | 人类确认或改由 seed 提供 |
| Q7 | `agent/write_intents.py` 只剩 5 行余量，先拆还是先压 | 先拆（照 04b/05b 先例） |

Q1–Q7 未定之前 F20 不具备开工条件。本文的作用是把这七件事从「没人想过」变成「有明确选项可拍板」。
