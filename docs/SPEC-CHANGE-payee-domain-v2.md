# SPEC-CHANGE · 转账域 v2（收款人主数据 + 两条统一关卡）

**变更类型**：① §1 **两张表加列**；② T20 的 **facts** 增加 `profiles`；③ §5 的**风控口径**更新。
**批准**：人类已批「串联版 + 可编辑卡面 + 同音匹配（新增依赖 pypinyin）」；设计见
`docs/superpowers/specs/2026-10-21-payee-domain-v2-design.md`。
**范围**：只动转账域（转账/定时/AA/送礼/收款人），不碰其他人的功能。

---

## 1. 契约变更（已完成，逐步提交在 `feat/t25-payee-whitelist`）

### §1 两张表加列（`schema.sql` 与规格 §1 逐字同步，守门测试已过）

```
payee  +tags / +whitelist_quota / +blacklist / +blacklist_reason
txn    +payee_id
```
- `payee.whitelist_quota`：白名单**专属额度**（整数分），NULL=无额度。
- `payee.blacklist`：黑名单（0/1），与 `is_whitelist` **互斥**（DAO 层强制）。
- `txn.payee_id`：**必须加** —— 原 `txn` 只有 `counterparty`（名字），按名字算白名单额度会把
  「同名的两个李四」合并计算。新转账回填它，种子流行为 NULL、DAO 回落到按名字（口径写进注释）。

### T20 的 facts 加 `profiles`（**data 未动**）

`data.items` 的 5 个键是**冻结契约**，故标签/黑名单/额度只放进 `facts["profiles"]`
（facts 本就是「给编排层的额外事实包」，加键是既有做法）。

### 新依赖

`pypinyin==0.55.0`（同音匹配用，人类已批）。

---

## 2. 风控口径更新（这是"行为"变更，不是接口）

### 白名单 = 一个**动态状态**，不是布尔值

```
黑名单                        → reject（转账/AA/送礼都直接拒，带原因）
白名单 + 金额 ≤ 剩余专属额度   → allow（L1 免码）
白名单 + 超额度 / 未设额度     → need_otp（至少 L2）
普通收款人                     → need_otp（走既有档位）
```

**覆盖关系**：人类需求原文写「白名单：在专属额度内转账，不需要验证码」，比旧 §5 的
`night(23:00-06:00)` 因子更明确。故**额度内免码、不再被 night 升档**。

✅ **已实现**（2026-10-22，`payee_risk.apply_quota` 增加 `factors` 入参）：
额度内 + 只命中「深夜」因子 → L1 + 「白名单专属额度内，深夜时段也免验证码」；
**短时多笔 / 金额激增 / 首次收款等异常因子仍照常升档**（只覆盖 night，不放松异常场景）。
卡面 4 处措辞已按新口径回写（表格说明 / 白名单确认卡 / 纯文本回执 / 表单追问）。

### AA：含非白名单人员 → 要码

发起人验**一次**（不是每人一次）。全白名单成员 → L1 免码。

### 送礼：收货人在黑名单 → 直接拒

「黑名单：禁止转账与送礼」。送礼不收「专属额度」这一层（额度口径只对转账定义）。

### 黑名单增删（F22c/F22d，2026-10-22 追加）

- 加黑 = **T28 `set_payee_blacklist`** · `payee_blacklist` · **L1 无 OTP**（加黑是阻断、风险低于加白）；
- 解除 = **T29 `unset_payee_blacklist`** · `payee_unblacklist` · **L2 + OTP**（人类需求原文「解除黑名单必须通过验证码确认」）；
- 黑白**互斥**在工具层强制（加黑清 `is_whitelist`）；
- 三个状态意图（加白/加黑/解除）的 SLOT_FILL **同一套解析关卡**（标签/同音/黑名单，
  `resolve_or_explain` 增加 `allow_blacklisted` 开关）—— 修掉「把房东加入白名单」解析失败的缺陷；
- 工具实现已拆到 `tools/payee_status.py`（原 `tools/payee.py` 349 行超红线），`payee.*` 旧引用不变。

---

## 3. 两条统一关卡（"串联"的本体，新增编排层模块）

| 模块 | 职责 |
|---|---|
| `agent/payee_resolve.py` | 解析关卡：标签 → 姓名精确 → **拼音全等（同音）** → 回落 T6 子串；黑名单命中直接 `blacklisted` |
| `agent/payee_risk.py` | 风控关卡：黑名单 → 专属额度 → 既有 `preview_transfer` 档位（**不重算**，只叠额度层） |

四个出口（`_shape_transfer` / `_shape_scheduled` / `_shape_aa` / `_prepare_gift`）都调这两条，
故「同一个人」在四个功能里给**一致**结论。

---

## 4. 兼容性影响

| 维度 | 结论 |
|---|---|
| 既有 25 个工具的**名字与签名** | 零改动（标签/同音放在编排层，避开了 T6/T17 的签名变更） |
| `data.items`（T20 冻结契约） | 零改动（只加 facts） |
| §5 档位表 | 语义更新：白名单免码以「专属额度」为界；AA 加「含非白名单要码」 |
| 测试 | `EXPECTED_COLUMN_COUNTS` 两处 + AA 4 条用例 + yaml aa-001..004 按新口径更新（**不是改测试凑绿，是规格变了**） |

---

## 5. 通知全员

- [ ] §1 两张表各加列（旧库 `SchemaDriftError` → `python -m data.seed --reset`）
- [ ] 新依赖 `pypinyin`（`uv sync` 会自动装）
- [ ] 风控新口径：白名单额度内免码（覆盖 night）；AA 含非白名单要码；送礼黑名单拒
- [ ] **共享文件提醒**：本改动动了 `agent/_write_transfer.py` / `_write_aa.py` / `_write_gift.py`，
      以及 `data/schema.sql`、`tools/payee.py` —— 合并时别跟别人覆盖

---

## 6. 2026-10-23 追加（PR #43 之后同一分支）

### F22e 删除收款人（T30 `delete_payee` · L1、无 OTP）

- 删的是联系人、不动钱、不授特权（与加黑同档）；历史流水**保留**（审计可回溯）；
  白/黑名单状态随行消失；已删除 → `NOT_FOUND`；归属 fail-closed。
- 意图 33 → 34（`payee_delete`）；收款人卡新增「删除」按钮。

### S5 可编辑卡片（设计 §4 落地，**不新增冻结契约**）

- `agent/card_draft.py`：`CardDraft`/`Field` 数据契约（业务状态在 agent 层）。
- `agent/write_flow.edit(session_id, updates)`：改关键字段 → **重走** shape → 解析/风控 → 出新卡
  （不是直接改卡）；OTP 阶段拒绝编辑。
- `resume()` 增加 `classify` 回调：聊天「金额改成 50 元」→ 分类同意图 → 合并旧卡字段出新卡；
  意图变了 → 维持旧行为。`orchestrator` 只在在途确认时传一次分类（LLM 只在此刻多调一次）。
- `interfaces/web/card_edit_ui.py`：网页编辑面板（逐字段输入 + 「按修改重新预检」）。
  可编辑字段表：转账（收款人/金额）、定时（+预约时间）、送礼（收货人/日期/预算）；
  AA/订阅/理财/白名单等意图不出面板。

### 转账执行回填（设计 §2 补漏）

- `execute_transfer` 事务内回填 `payee.last_used_ts` + `txn.payee_id`：
  额度从此按 **id** 聚合（不再靠名字兜底），「首次收款」因子转完一次就消失。
  `data/dao.insert_txn` 加 `payee_id` 参数（data 层内部，非冻结契约）；

### F22f AA 收款口径调整 + 按人金额（T31，2026-10-23 人类实测反馈）

- **AA 收款免验证码**：收款方向（钱进来不是出去）→ L1，含非白名单成员也不再要码
  （推翻 S4d 的「含非白名单要码」）；**黑名单成员仍然不能收款**（解析关卡 FORBIDDEN）。
- **按人金额**：「王五 100 元、赵六 200 元」确定性解析 → 卡面逐人明细 + 合计校验
  （与总额不符直接报错；没报总额 → 合计为总额）；一键均摊 = 缺省，卡面明示。
- 执行侧新增 **T31 `create_aa_request_with_amounts`**（T9 签名冻结不加参数 → 新工具
  不破契约）；§2 30 → 31 工具；分类器加 `amounts` 槽位。
- 收款人卡按钮改**确定性直达** `write_flow.start`（修「在途卡串台：点拉黑弹转账卡」）；
  添加收款人表单**预填**（聊天句已说出的姓名/手机号）。
  `data/dao.py` 仍 300 行顶格（有测试钉着）。

---

## 6. 验证

```bash
uv run pytest -q          # 1460 passed, 0 failed
bash scripts/verify.sh    # 用例 49/49 · 冒烟 4/4 · 红线 100 passed · 全部通过
```
