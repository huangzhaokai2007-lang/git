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
⚠️ 这与此前卡面写的「风险因子仍会升档」冲突 —— 措辞需按新口径再改一版（未做）。

### AA：含非白名单人员 → 要码

发起人验**一次**（不是每人一次）。全白名单成员 → L1 免码。

### 送礼：收货人在黑名单 → 直接拒

「黑名单：禁止转账与送礼」。送礼不收「专属额度」这一层（额度口径只对转账定义）。

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

## 6. 验证

```bash
uv run pytest -q          # 1460 passed, 0 failed
bash scripts/verify.sh    # 用例 49/49 · 冒烟 4/4 · 红线 100 passed · 全部通过
```
