# 转账域 v2 设计（串联版 + 可编辑卡面 + 同音匹配）

**批准**：人类已批「串联版，卡面可编辑：做，同音匹配：要」（2026-10-21）。
**范围**：只做**黄自己的 6 个功能**（F15/F16/F17/F20/F21/F22 + 白名单），不碰其他人的功能。
**工作目录**：`repo-whitelist/`（git worktree，避免与另外 3 个会话互相踩）。

---

## 0. 一句话目标

> 把 6 个孤立功能重做成**一个转账域**：一份收款人主数据 → 4 个出口（转账/定时/AA/送礼）
> 复用**同一条**「解析 → 风控 → 验证码 → 执行」流水线，共用**同一套**卡片。

---

## 1. 范围（做什么 / 不做什么）

| ✅ 做 | ❌ 不做（留 v3） |
|---|---|
| 收款人**标签**（多个）+ 同音/标签指代 | 白名单**有效期**（用户已明确「不参与风控」，最后加） |
| 白名单**专属额度** + 已用/剩余 | AA **进行中/每人已付/24h 失效/提醒/撤销** |
| **黑名单**（拒绝转账/AA/定时/送礼 + 原因） | 预约**修改/立即执行/周期支付/执行前提醒** |
| **统一收款人解析关卡**（一条实现，4 出口共用） | 送礼**订单号状态机/查询筛选** |
| **统一风控关卡**（白名单额度内免码 / 普通要码 / 黑名单拒） | 每张卡的 8 状态徽章 |
| **统一验证码关卡**（含 3 次锁定、通过后回原卡继续） | 同音匹配的**多音字消歧**（只做拼音全等 + 声母韵母近似） |
| **卡面可编辑**（关键字段卡内改 → 重走校验） | |
| **查询卡 + 行内操作入口**（转账/加白/加黑） | |

**明确保留的既有约束**（不改）：验证码固定 `123456`、转账档位由 `guard/` 纯代码判、
所有回执数字来自事实包、白名单有效期不参与风控。

---

## 2. 数据层改动

### `payee` 表加列（迁移，不新建表）

| 列 | 类型 | 说明 |
|---|---|---|
| `tags` | TEXT | 逗号分隔（demo 够用；不建关联表，省一次 JOIN） |
| `whitelist_quota` | INTEGER | 白名单专属额度（**分**）；NULL = 未设 |
| `blacklist` | INTEGER | 0/1；黑名单 |
| `blacklist_reason` | TEXT | 黑名单原因（可空） |
| `last_txn_at` | TEXT | 最近成功转账时间（可空，由执行时回填） |

**已用额度不存表**：按 `txn` 聚合 `payee_id + 本月` 算出 ✅（避免一份数据两处真相）。

### 约定
- **白名单与黑名单互斥**：工具层强制（同一行不能既有 `is_whitelist=1` 又有 `blacklist=1`）。
- `whitelist_quota` 必须为正整数分；为 NULL 视为「无专属额度」（= 普通，要验证码）。

---

## 3. 三条横向关卡（**这是"串联"的本体**）

### 关卡 1 · 收款人解析（新建 `agent/payee_resolve.py`）

```python
resolve(text_or_tag) -> ResolveResult(kind="unique|ambiguous|none|blacklisted", payees=[...])
```
- 依次尝试：**标签精确命中** → 姓名精确 → **拼音全等**（同音）→ 姓名/手机号子串
- 命中唯一 → 直接用；命中多个 → 出**解析卡**；零命中 → 提示 + 「新增收款人」出口
- **黑名单命中 → 直接 `Rejected`**（不进入解析卡）
- 4 个出口（转账/定时/AA/送礼）**都调它**，不再各写一遍
- 会话内记忆：「本次刚解析过/刚添加的收款人」直接命中（复用 `session_memory`）

### 关卡 2 · 风控（扩 `guard/permission`）

```python
assess_payee_risk(payee, amount_cents, today_out) -> RiskVerdict(
    decision="allow|need_otp|reject", tier="L1|L2|L3",
    reason="whitelist_within_quota|whitelist_exceeded|non_whitelist|over_limit|blacklisted",
    facts={quota_cents, used_cents, remain_cents})
```
- 黑名单 → `reject`
- 白名单 **且** 金额 ≤ 剩余专属额度 **且** 未超单笔/今日限额 → `allow`（L1、免码）
- 白名单但**超专属额度** → `need_otp`（L2，reason=whitelist_exceeded）
- 普通 / 超限 → `need_otp`
- **不判白名单有效期**（用户明确）

### 关卡 3 · 验证码

复用现有 `confirm_card`（`note_otp_error` / `is_locked` / 3 次锁定 / 通过后 `resume` 回原卡）。
只做一件事：把「触发条件」统一成关卡 2 的 `decision == "need_otp"`。

---

## 4. 卡片（可编辑）

现状：卡片是 `confirm_card.render` 出来的 **markdown 纯文本**，不可交互。
目标：`interfaces/web/` 里加一个**可编辑卡片组件**：

- 结构：`CardDraft(kind, title, status, fields=[Field(name, value, editable, sensitive)], actions=[...], hint)`
- 字段可编辑 → 提交后**重走** `shape → 解析关卡 → 风控关卡`（不是直接改值）
- 敏感字段一律**脱敏展示**（`138****1234`、`**** **** **** 8888`）
- 状态：`draft / resolving / need_otp / confirmed / executing / done / failed / cancelled`

**分层**：`CardDraft` 定义在 `agent/`（草稿是业务状态），**渲染**在 `interfaces/web/` ✅
（界面不写业务、不派生数字）。

---

## 5. 分阶段执行（每阶段独立可演示、独立提交）

| 阶段 | 内容 | 验收 | 估时 |
|---|---|---|---|
| **S1** | 数据层：`payee` 加 5 列 + 迁移 + DAO 原语 + `pypinyin` 依赖 | 迁移幂等；`tags/quota/blacklist` 可读写；既有 1436 条测试全绿 | 1 天 |
| **S2** | 解析关卡（含同音、标签、黑名单拒绝）+ 会话记忆 | 单测：标签唯一/多命中/同音/黑名单/零命中；4 出口尚未接入 | 1.5 天 |
| **S3** | 风控关卡（专属额度口径）+ 验证码触发统一 | 单测：额度内免码 / 超额要码 / 黑名单拒 / 超限要码 | 1 天 |
| **S4** | 4 个出口接入同一流水线（转账先跑通，其余跟上） | yaml 用例：4 个出口对同一收款人给出**一致**的风控结论 | 1.5 天 |
| **S5** | 可编辑卡片组件（`CardDraft` + web 渲染） | AppTest：改金额后重走风控；脱敏正确；3 次错码锁定 | 2 天 |
| **S6** | 查询卡 + 行内操作入口（转账/加白/加黑） | 点「加白」进白名单流水线；筛选全部/白/黑 | 1 天 |
| **合计** | | | **≈ 8 天** |

---

## 6. 必须走的流程

1. **SPEC-CHANGE**：`add_payee` 签名要加 `tags / whitelist_quota / blacklist / reason`；
   `resolve_payee` 要支持标签与拼音 → 两处都要存档（`docs/SPEC-CHANGE-*.md`）并通知全员。
2. **新依赖**：`pypinyin`（人类已批 ✅）—— 记进 `pyproject.toml` + 在 SPEC-CHANGE 里注明。
3. **`data/dao.py` 已 300 行顶格** → 加 DAO 原语前必须先拆（`data/payee_dao.py`）。
4. **不碰别人的功能**：只改转账域内的文件；`write_flow` / `orchestrator` / `classifier` 是共享文件，
   改之前先看 `git status` 有没有别人的未提交改动。

---

## 7. 风险

| 风险 | 缓解 |
|---|---|
| 与另外 3 个会话撞共享文件 | 只在 `repo-whitelist/` worktree 里做；提交前查 `git status` |
| 同音匹配产生**假阳性**（把"王晓红"匹配成"王小宏"） | 只做**拼音全等**，不做模糊编辑距离；多命中就出解析卡，不猜 |
| 卡面可编辑让"卡上数字必须来自 facts"失效 | 编辑后**不直接改确认卡**，而是把新值重新走一遍 `shape → 风控`，由 tools 重新出事实包 |
| 阶段跨度大、中途断档 | 每阶段结束必须：门禁绿 + 提交 + 可演示 |

---

## 8. 每阶段的递交格式

```
uv run pytest -q           → 真实输出
bash scripts/verify.sh     → 六段真实输出
真实模型跑一遍本阶段的新流程 → 贴回执
```
**没跑通不许说完成**（CLAUDE.md）。
