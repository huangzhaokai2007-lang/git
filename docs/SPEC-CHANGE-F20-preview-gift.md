# SPEC-CHANGE · F20 新增 T19 `preview_gift`

**变更类型**：`docs/01-接口规格.md` §2 工具契约**新增一个只读工具**（18 → 19 个）。
**提案与批准**：F20 设计文档（`docs/superpowers/specs/2026-10-12-f20-gift-plan-design.md`）的 Q1–Q5；
**人类（项目负责人 / 板块负责人）于实施前批准 Q1–Q5 按建议执行**，其中 Q2 = 「新增 T19」。
**变更执行**：随 F20 实现一并提交。

---

## 1. 改了什么

| 位置 | 改动 |
|---|---|
| `docs/01-接口规格.md` §2 标题 | `（18 个，名字与字段名冻结）` → `（19 个，…）` |
| `docs/01-接口规格.md` §2 表 | 在 T18 之后**追加** T19 行 |
| `tools/cross_scene.py` | 实现 `preview_gift(contact, date, budget)` |

T19 契约：

```
T19  preview_gift(contact,date,budget)
     入参：与 T16 同形（contact 联系人 / date ISO8601 日期 / budget 整数分）
     data 关键字段：items[{item_id,name,price}], total, available, balance
     权限档：L0（只读）
     语义：T16 的**只读预览** —— 挑礼物 + 算合计 + 读余额
           **不锁资金、不写审计、不生成 plan_id / lock_id**
```

**已存在 18 个工具的名字、签名与返回字段一字未动**，本次是**纯追加**。

---

## 2. 为什么必须新增（而不是让编排层自己算）

F20 验收第 2 条要求「**展示模拟清单和预算**」，而确认卡上的每个数字都必须能在事实包里找到
（铁律 2 + `guard.facts_check` 的 `card_numbers_outside_facts` 守门）。

T16 `plan_gift` **是写操作** —— 一调就锁资金、生成 `plan_id`/`lock_id`、写审计，
**不能拿它当预览**（否则直接违反 F20 验收第 1 条「确认与 OTP 前不锁定资金」）。

于是只剩两条路：

| 方案 | 评价 |
|---|---|
| 编排层自己复刻「挑礼物 + 算合计」的逻辑 | **否决**：`tools/cross_scene.py:_pick_items` 就成了两份实现，改一处必漏另一处（口径漂移是团队反复栽过的坑） |
| **新增 L0 只读预览（采纳）** | 与 F18 设计里同构的论证一致：「按仓库铁律业务数字须来自工具层事实包，故建议新增工具」 |

实现上 T19 与 T16 **共用** `_pick_items` / `_wanted_date` / `_savings_row` / `_require_available`，
因此不会出现「预览说买得起、执行时被拒」这类分叉。

---

## 3. 兼容性影响

| 维度 | 结论 |
|---|---|
| 既有 18 个工具 | **零影响**（纯追加，无重命名、无字段增删） |
| 数据库结构 | **零影响**（T19 只读，不建表、不写任何表） |
| 已接通的写意图 | **零影响**（T19 只被 `gift_plan` 的 `prepare` 调用） |
| `tests/test_spec_counts.py` | 标题数与表行数**必须同时改**，否则该守卫报警 —— 本次两处已同步（19 == 19 行，序号连续） |
| 权限矩阵 §5 | 无需改动：T19 为 L0 只读，§5 只约束写操作 |

---

## 4. 重要：与 F18 的 T19 编号冲突

莫的 F18 设计（`docs/superpowers/specs/2026-10-03-f18-subscription-reminder-design.md`）**同样申请
`remind_subscription` 为 T19**，但该提案**尚未落地**（本次变更前，规格 §2 表内根本没有 T19 行）。

- 本变更按「先到先得」占用 **T19**；
- **F18 若实施，需顺延为 T20**，由**队长一次性裁决**；
- 该冲突已在规格 T19 行的备注列与本文件同时标出，避免再次出现「两份设计各写各的编号」。

---

## 5. 通知全员（规格开头第 3–4 行的要求）

- [ ] 在团队频道公布本次 `SPEC-CHANGE`（标题 18 → 19、新增 T19）
- [ ] 提醒莫：F18 的 T19 提案需顺延为 T20，请队长裁决
- [ ] 提醒本周集成人员：`tools/cross_scene.py` 由本变更修改（函数为纯追加）

---

## 6. 验证

```bash
uv run pytest tests/test_spec_counts.py -v      # 3 passed：标题数 == 行数、序号连续、§3 与分类器一致
uv run pytest tests/test_gift_plan_flow.py -v   # F20 新增 8 条
uv run pytest tests/test_cases.py -v            # 用例通过率 42/42
bash scripts/verify.sh
```

真实模型冒烟：

```bash
uv run python -m app.cli "2026-10-20 给张三送生日礼物，预算500元"
# → 送礼计划确认卡（只调 preview_gift，available 未动）；工具名 = preview_gift
```
