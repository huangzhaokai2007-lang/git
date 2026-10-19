# F16 定时转账行为设计

W1 周报（`docs/weekly/2026-10-04.md:39`）的逾期待办原文：「**黄已给 F16 确定预约触发、时区、持久化与取消口径，
并记录需审批的接口变化。**」F16 验收第 1 条亦要求「先确定预约持久化、执行触发、时区与取消规则，
**规格变化由队长审批**」。本文定这四件事与契约影响，**不含代码实现**。

---

## 一、现状实测：不是「功能缺失」，是「**做错了**」

`docs/features/F16-scheduled-transfer.md:11` 写「意图路由已有，但工具拒绝 schedule」。实机核对后发现更严重的情形：

| # | 实测 | 证据 |
|---|---|---|
| 1 | 真实模型**正确**理解了预约 | `'明天给王五转100元'` → `slots={'payee':'王五','amount':100,'schedule':'明天'}`；`'下周一给张小美转200元'` → `schedule='下周一'` |
| 2 | 编排层**把 `schedule` 丢掉了** | `agent/_write_transfer.py:_prepare_transfer` 调的是 `preview_transfer(filled["payee_id"], filled["amount_cents"])` —— **两个参数，没有 schedule** |
| 3 | 于是出的是**即时转账**确认卡 | 实机输出：`意图：定时转账 … **预计到账：实时到账** · 权限档：L1`，`已执行=否` |

**后果**：用户说「明天转」，系统把日期**静默丢弃**，确认卡上写着「实时到账」——**L1 档点一下「确认」，钱立刻就出去了**。

这直接违反 F16 验收第 2 条「**预约时间到达前不能按即时转账扣款**」与第 3 条的精神。
**故 F16 的首要目标是「让系统不再默默做错」，而不是「新增一个功能」。**

另外 `tools/transfer.py:113-115` 里 `preview_transfer` 对 `schedule` 是**显式拒绝**
（`INVALID_ARGUMENT`「本卡还不支持定时转账或拆分转账」），且规格 §2 T7 的冻结签名
**本来就带 `schedule=None`** —— 参数位留着，语义未定。这是本设计要填的坑。

---

## 二、触发：全项目无调度器 → 建议**惰性结算**（lazy）

核对结果：`scripts/`、`agent/`、`app/` 下**没有任何调度器**（无 APScheduler、无 `threading.Timer`、
无 cron；`asyncio` 只出现在 `scripts/api_smoke.py` 的 ASGI 自测里）。

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（建议）** | **惰性结算**：每次会话轮次入口先把「已到期」的预约结算掉（幂等），再处理本次请求 | **零新增基础设施**；评测环境仍「一条命令跑起来」；时间可被 `_now` 钉住故可测；重启天然恢复（状态在库里） |
| B | 后台线程 / `threading.Timer` | 要给 Streamlit / FastAPI / CLI 三种入口各起一套，且 CLI 是**一次性进程**（跑完就退），定时器随之消失；评测期进程生命周期不可控 |
| C | 引入 APScheduler 等 | **违反铁律 6**（新依赖先问）且评测环境不允许多一步安装 |

**A 的代价（如实登记）**：到期时刻与用户下次交互之间有空窗 —— **用户不打开应用，预约就不会执行**。
demo 期这是可接受的取舍（时间锚点是 `data.seed.AS_OF`，本就非真实时钟），但它**不是生产可用的调度器**，
必须在交付说明里写明。建议同时给演示提供一条显式触发路径（见「待拍板 Q3」）。

**结算挂在哪**：`agent/orchestrator.handle` 最前面（分类之前）。开销是一次带索引的 `SELECT`，
无到期预约时不写任何表（**不能因为「顺手」就每轮写审计**）。

---

## 三、时区：与全仓**同一口径**，不引入 tz-aware

| 项 | 口径 |
|---|---|
| 时间源 | `tools/transfer._now()` = `datetime.now()`（**naive 本地时**；单独抽出来正是为了让测试钉住它，已有先例） |
| demo 时间锚 | `data.seed.AS_OF` = **2026-09-12**（`cross_scene._wanted_date` 用它判「不能早于今天」） |
| 时区 | 一律按**本地时**解释；**不落库时区字段、不做 tz 换算** |
| 原因 | 引入 tz-aware 会与既有 `_now()` / `AS_OF`（都是 naive）分叉，且要改所有比较点 —— 风险远大于收益 |
| 测试 | 用既有手法钉 `_now`（`tests/conftest.py` 的 `clock` 夹具） |

**登记为限制**：跨时区用户与 DST 未处理。

---

## 四、持久化：**必须新增一张表**（DDL 变化 ⇒ `SPEC-CHANGE`）

现状 `data/schema.sql` 共 **12 张表**（user / account / txn / card / subscription / wealth_product /
holding / payee / audit_log / risk_event / idempotency / rate_limit）—— **没有任何表能存预约**。

建议新增（风格对齐既有 DDL：TEXT 列、无外键）：

```sql
CREATE TABLE scheduled_transfer (
  id TEXT PRIMARY KEY,              -- sch_<uuid12>
  user_id TEXT NOT NULL,
  payee_id TEXT NOT NULL,
  amount INTEGER NOT NULL,          -- 整数分（禁浮点）
  execute_at TEXT NOT NULL,         -- ISO8601，本地时
  status TEXT NOT NULL,             -- pending / success / failed / cancelled
  tier TEXT NOT NULL,               -- 预约时刻定档（见第五节）
  created_at TEXT NOT NULL,
  settled_at TEXT,                  -- 结算时刻；pending 时为 NULL
  txn_id TEXT,                      -- 成功后回填
  fail_reason TEXT                  -- 失败原因码
);
```

**幂等 / 重复触发 / 重启恢复**（验收第 3 条）—— 用**条件更新抢锁**（同 T8 在卡 14b-6 的手法）：

```sql
UPDATE scheduled_transfer SET status='running', settled_at=? WHERE id=? AND status='pending'
```

受影响行数 **0 = 已被抢先结算** → 直接返回库中既有结果，**绝不重复扣款**。
重启恢复天然成立：状态在库里，`pending` 行下次轮次继续尝试。

---

## 五、到期时**要不要重新定档**？（最需要拍板的一条）

预约时刻由用户确认过一次；但**到期时刻用户不在场**，无法再做确认、更无法输入 OTP。

| 方案 | 说明 | 建议 |
|---|---|---|
| **冻结预约时刻的档位**（建议） | 到期只**重检硬约束**（收款人仍属于本人、余额充足、单笔上限），**不因夜间/高频等降级因子升档** | 采纳。否则「晚上 11 点到期」会被判 L3 → 需要人工复核 → **预约永远无法完成** |
| 到期重新定档并要求确认 | 到期转人工/待办 | 会让预约功能名存实亡 |

**登记为风险**：这等于「预约期间的档位被冻结」。建议在规格 §5 补一句口径说明。
**降级因子中的「夜间」在同一天内会变化**，故这条必须有明文，不能靠实现默认。

---

## 六、执行路径：**300 秒的凭证装不下「明天」**（核心契约问题）

规格 §2 T7 备注原文：**「`preview_token` 有 TTL 300s 且绑定参数」**。而预约可能是几小时/几天后 ——
**到期时那个 token 早已过期**，且期间余额、日限额、收款人状态都可能变。

因此**不能**「预约时签 token，到期拿 token 执行」。到期必须**重新取数**。写路径四步映射为：

| 四步 | 定时转账对应动作 |
|---|---|
| PRECHECK（只算不执行） | `preview_transfer(payee_id, amount, schedule=...)` —— **填上 `schedule` 语义**：校验时间合法、算档位、**不落库** |
| 用户确认（必要时 OTP） | 确认卡显示「预约时间 / 收款人 / 金额 / 档位」，措辞**严禁**「实时到账」 |
| EXECUTE（写） | **新增工具**：消费 token → **落一条 `pending` 预约**（**此刻不扣款**），返回 `schedule_id` + `execute_at` |
| 到期（异步） | **新增工具**：结算 —— 条件更新抢锁 → 重检硬约束 → 一个事务内扣款 + 写流水 + 写审计 |

**关键结论：需要新增 2 个工具**（`preview_transfer` 只是补语义，签名不变）。

---

## 七、取消：需**同时**新增工具与 §3 意图

现状**没有任何取消预约的工具**，规格也没有对应意图。取消是写操作，
**必须走工具层**（`agent/` 直接改表 = 越层 = 违规），故：

- 新增工具 `cancel_scheduled(schedule_id)`：仅 `pending` 可取消 → `cancelled`（**已扣款的不能取消**，只能走退款——不在本卡范围）；
- **需在 §3 意图清单新增一个意图**（如 `transfer_scheduled_cancel`）并同步 `classifier.INTENT_LABELS`
  —— `tests/test_spec_counts.py` 有守卫钉着「§3 与分类器内容与顺序一致」，两处必须同时改；
- 取消的档位**建议 L1**（会话内确认卡、不要 OTP）：它是**撤回一个尚未发生的动作**，风险低于发起；
  且「取消预约」不涉及资金移动。**待拍板 Q4。**

---

## 八、契约影响：`SPEC-CHANGE` 清单（待队长审批）

| # | 变更 | 说明 |
|---|---|---|
| 1 | **§2 T7 补 `schedule` 语义** | 签名不变（`schedule=None` 本来就在），只填「传了怎么解释」：ISO8601 本地时、必须晚于 `AS_OF` |
| 2 | **§2 新增一个「落预约」工具** | 消费 `preview_token`，落 `pending` 行，返回 `schedule_id` / `execute_at`。**不扣款** |
| 3 | **§2 新增一个「结算」工具** | 系统触发（不对应任何用户意图）；条件更新抢锁 + 重检硬约束 + 扣款 |
| 4 | **§2 新增 `cancel_scheduled`** | 用户触发；仅 `pending` 可取消 |
| 5 | **§3 新增取消意图** | 与 `classifier.INTENT_LABELS` 同步（有机器守卫） |
| 6 | **DDL 新增 `scheduled_transfer` 表** | 见第四节 |
| 7 | **§5 补口径说明** | ① 预约档位在预约时刻冻结；② 取消定档 |

T7 签名**不改**、T8 **不用**（token 过期）、既有 12 张表**不改**（只新增一张）。

---

## 九、⚠️ 编号冲突（第三次出现，建议一次裁决到底）

规格 §2 目前表已到 **T19**，且 T19 已被 **F20 的 `preview_gift`** 占用（PR #19）。未落地的提案：

| 提案 | 想要编号 | 现状 |
|---|---|---|
| F18 订阅提醒 `remind_subscription` | 原申请 T19 | T19 被 F20 占用 → 需顺延 |
| **F16 落预约** | — | 本设计需 1 个 |
| **F16 结算** | — | 本设计需 1 个 |
| **F16 取消** | — | 本设计需 1 个 |

**F16 一家就要 3 个编号**，加上 F18 的 1 个，共 4 个待分配（T20–T23）。
建议队长**一次性裁决**并把结果写回规格，避免每张卡各写各的编号（该坑已栽三次）。

---

## 十、实现路径与已知约束（审批通过后执行）

| 步骤 | 文件 | 注意 |
|---|---|---|
| 1 | `data/schema.sql` + 迁移 | 新增表；`tools/transfer.py` **已 296 行**（红线 300），新函数**必须放新模块**（照 `_aa_split.py` / `_transfer_risk.py` 先例，建议 `tools/_transfer_schedule.py`） |
| 2 | `tools/transfer.py` | `preview_transfer` 去掉「传了就拒」，改为校验 + 算档位 |
| 3 | `agent/_write_transfer.py` | `_prepare_transfer` 按意图传 `schedule`；`transfer_scheduled` 需要**独立的五个纯函数**（不再与 `transfer_single` 共用） |
| 4 | `agent/write_intents.py` | `transfer_scheduled` 描述符改为自己的函数组；`execute` 调新工具 |
| 5 | `tools/_transfer_schedule.py`（新） | 落预约 / 结算（CAS + 单事务）/ 取消 |
| 6 | `agent/orchestrator.py` | 轮次入口挂惰性结算（**不与分类耦合**，建议抽 `agent/scheduled_runner.py`） |
| 7 | `agent/classifier.py` + 规格 §3 | 新增取消意图（两处同步，有守卫） |
| 8 | `tests/` | 到期前不扣款 / 到期后扣一次 / 并发与重启只结算一次 / 取消后不再结算 / 到期余额不足 / 日期早于锚点 / 档位冻结 |
| 9 | `tests/cases/orchestrator.yaml` | 新增 `sch-*` 用例组 |

---

## 十一、待拍板清单

| # | 问题 | 建议 |
|---|---|---|
| Q1 | 触发采用惰性结算，还是上后台调度器 | **惰性结算**（零新基础设施；代价=有空窗，登记） |
| Q2 | 时区是否引入 tz-aware | **不引入**，沿用 naive 本地时 + `AS_OF` |
| Q3 | 惰性结算是否需要一条**演示用显式触发**入口 | 需要（否则演示「到期执行」只能靠改系统时间） |
| Q4 | 取消的档位，以及是否需要 OTP | **L1 会话内确认卡、不要 OTP** |
| Q5 | 到期是否重新定档 | **冻结预约时刻档位**，到期只重检硬约束 |
| Q6 | 到期余额不足的重试策略 | **不重试**，置 `failed` + 记原因 + 下次交互告知（重试属另一张卡） |
| Q7 | 取消是否需要「已扣款后退款」 | **不做**（本卡只管 `pending` → `cancelled`） |
| Q8 | T20–T23 四个编号怎么分 | 队长一次性裁决（含 F18 的 T20 提案） |
| Q9 | 惰性结算挂在 `orchestrator.handle` 入口是否可接受 | 接受；**不与分类耦合**，抽独立模块 |
| Q10 | 演示口径：预约时间说成 `YYYY-MM-DD` 还是支持「明天/下周一」 | 现阶段 `agent/period.resolve_day` **只支持 ISO 与相对月**；扩展相对日属另一张卡 |

Q1–Q10 未定之前 F16 不具备开工条件。本文的作用是把这十件事从「没人想过」变成「有明确选项可拍板」。

**另注**：无论本方案是否获批，**当前「说预约却立刻扣款」的行为都应在下一次合并前先堵住** ——
最低成本的做法是让 `transfer_scheduled` 在缺少合法 `schedule` 时**拒绝出确认卡并说明**
（而不是当作即时转账），这样至少不会静默做错。该「止损」改动与本方案解耦，可单独提。
