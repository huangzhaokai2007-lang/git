# SPEC-CHANGE · F16 定时转账（T21/T22/T23/T24 + 一张新表 + 一个新意图）

**变更类型**：① 规格 §1 **新增一张表**（12 → 13）；② §2 **新增四个工具**（20 → 24）；
③ §3 **新增一个意图** `scheduled_cancel`（27 → 28）。
**提案与批准**：`docs/superpowers/specs/2026-10-15-f16-scheduled-transfer-design.md` 的 Q1–Q10，
**人类已批「Q1–Q10 按建议批」**。
**交付范围**：**完整交付** —— Stage 1（存储 + 工具层）+ Stage 2（编排层：预约可用、到点自动执行、可取消）。
止损（一律拒绝）**已移除**。

---

## 1. 改了什么

| 位置 | 改动 |
|---|---|
| `docs/01-接口规格.md` §1 | 追加 `scheduled_transfer` 的 DDL（与 `data/schema.sql` **逐字一致**，有机器守卫钉着） |
| `docs/01-接口规格.md` §2 标题 | `（20 个，…）` → `（24 个，…）` |
| `docs/01-接口规格.md` §2 表 | 追加 T21 / T22 / T23 / T24 四行 |
| `docs/01-接口规格.md` §3 | 意图清单在 `transfer_scheduled` 之后插入 `scheduled_cancel` |
| `data/schema.sql` / `data/db.py` | 建表语句 + `TABLES` 12 → 13 |
| `tools/_transfer_schedule.py` | T21/T22/T23/T24 的实现 |
| `tools/_scheduled_common.py` | 状态机 / 行读取 / 抢锁 / 事实包 / T7 参数校验（前者超红线后拆出） |
| `tools/transfer.py` | **T7 补 `schedule` 语义**：不再「传了就拒」，改为校验「必须是晚于当前时间的 ISO8601」；`split_with` 仍拒 |
| `agent/_write_transfer.py` | 定时转账族（shape/prepare/card/plain/book）+ 取消族（shape/prepare/card/plain/execute） |
| `agent/scheduled_runner.py` | **惰性结算器**（Q1/Q9） |
| `agent/orchestrator.py` | `handle` 拆薄包装：**先结算 → 再处理 → 告知句拼在回执前** |
| `agent/write_intents.py` / `agent/classifier.py` | 接两个意图（`transfer_scheduled` 换真族、新增 `scheduled_cancel`），三处清单同步 |
| `guard/permission.py` | `INTENT_BASE_TIERS` 登记 `scheduled_cancel: L1`（Q4） |

**既有 20 个工具的名字、签名与返回字段一字未动**（T7 只补语义、不改签名 —— `schedule=None` 本来就在冻结签名里）。

---

## 2. 四个新工具

```
T21  schedule_transfer(preview_token, otp=None)   落 pending 预约。**此刻不扣款**。
     闸门顺序同 T8：凭证/归属 → 幂等 → TTL 300s → OTP → L3 拒绝；预约行+审计+凭证 CAS 同事务。

T22  execute_scheduled(schedule_id)               系统触发（不对应任何用户意图）。
     抢锁（WHERE status='pending'）+ 扣款 + 流水 + 审计同事务；Q5 不重新定档、Q6 失败不重试。

T23  cancel_scheduled(schedule_id)                仅 pending 可取消；**取消不动钱**（Q7 不做退款）。

T24  list_scheduled(status=None)                  L0 只读；供取消流程取「取消哪一笔」的事实包。
```

---

## 3. Q1–Q10 的落地对照

| # | 决定 | 落点 |
|---|---|---|
| Q1 | **惰性结算**（不引入调度器） | 工具层只提供原语；结算器在 Stage 2 的 `agent/scheduled_runner.py` |
| Q2 | **不引入 tz-aware**，沿用 naive 本地时 | DDL 的 `execute_at` 注释 + `_wanted`/`_now` 口径 |
| Q3 | 需要演示用显式触发入口 | **Stage 2** |
| Q4 | 取消档位 L1、不要 OTP | **Stage 2**（取消意图） |
| Q5 | **档位冻结**在预约时刻 | DDL 的 `tier NOT NULL` + T22 **不重新定档**、只重检硬约束 |
| Q6 | 到期失败**不重试** | T22 失败 → `_mark_failed`（独立写入，不受结算事务回滚影响） |
| Q7 | **不做**已扣款后退款 | T23 对 `success` 返回 `INVALID_STATE` |
| Q8 | 编号：T21/T22/T23 | T19 = F20、T20 = 白名单（查看白名单），故 F16 用 T21–T23；**F18 需从 T24 起** |
| Q9 | 结算不与分类耦合 | Stage 2 抽独立模块 |
| Q10 | 相对日（明天/下周一）扩展属另一张卡 | T7 只收 ISO8601（`YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM`） |

---

## 4. 兼容性影响

| 维度 | 结论 |
|---|---|
| 既有 20 个工具 | **零影响**（纯追加 + T7 补语义不改签名） |
| 既有表 | **零影响**（只新增一张表） |
| `tools/transfer.py` 行数 | 296 → **298**（仍在 300 红线内；`schedule` 校验放新模块 + 延迟 import 避开循环依赖） |
| `tests/test_db.py` / `test_dao_core.py` | 表数断言 12 → 13（**契约扩展，须同步**） |
| `tests/test_tools_transfer.py` | 「schedule 传了就拒」的用例改为「schedule 支持」，并**改名**让名字与行为一致 |
| `tests/test_db.py::test_schema_sql_is_verbatim_copy_of_spec` | §1 的 DDL 必须与 `schema.sql` **逐字一致** —— 本次两处同步 |

---

## 5. 剩余限制（如实登记）

- **`data/dao.py` 已正好 300 行**，加不下 `scheduled_transfer` 的 DAO 原语，故 T21/T22/T23 **直查 SQL**
  （仓内先例：`tools/cross_scene.py:_savings_row`）。补原语并拆 `dao.py` 属另一张卡。
- **T21/T22 未接写入限流**（T8 有 `tool_guard.check_write_rate`）。T22 是系统触发、T21 不扣款，
  故本次未加；若审核认为需要，Stage 2 一并补。

---

## 6. 通知全员

- [ ] 团队频道公布：§1 新增 `scheduled_transfer` 表（13 张）；§2 新增 T21/T22/T23（23 个工具）
- [ ] 提醒：**F18 的编号提案需从 T24 起**（T19=F20、T20=白名单、T21–T23=F16）
- [ ] 提醒本周集成人员：`tools/transfer.py` 由本变更修改（T7 补语义，签名不变）

---

## 7. 验证

```bash
uv run pytest tests/test_tools_scheduled_transfer.py -v    # 新增 13 条
uv run pytest tests/test_db.py tests/test_dao_core.py tests/test_tools_transfer.py -v
uv run pytest tests/test_spec_counts.py -v
bash scripts/verify.sh
```
