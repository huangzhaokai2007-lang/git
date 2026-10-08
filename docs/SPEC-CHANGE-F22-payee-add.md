# SPEC CHANGE F22 收款人自助添加（`payee_add` 定档）

用户已确认 `payee_add` 的 §3 意图登记与 §5 定档，授权以 `SPEC-CHANGE` 补入冻结规格。本次只补文档口径与一处档位表登记，**不改任何工具签名、返回字段或数据库结构**。

## 背景：card-20 承诺了三处规格变更，只落地了一处

`docs/cards/card-20.md` 第 24–25 条要求：

1. §2 加 T17 `add_payee`；
2. §3 意图清单加 `payee_add`（槽位 `name` / `phone` 可选）；
3. §5 档位：`payee_add` → L1，且不发确认卡、不要 OTP。

**实际只完成了第 1 条。** 第 2、3 条长期缺失，后果有二：

- `agent/classifier.py` 的注释写着「规格 §3 逐字抄录，不得增删」，但清单里有 `payee_add` —— **该声明为假**，且已持续三周（`docs/features/README.md` 曾以「本次仅记录差异，不修改冻结规格」留档）；
- `guard/permission.py` 的 `INTENT_BASE_TIERS` 注释是「§5 表里逐字点名的写意图」，而 `payee_add` 不在其中 → **未传 `tier` 时按 fail-closed 取 L3** 并打 warning。现有实现靠 `agent/payee_flow.py` 显式传 `tier="L1"` 绕过 —— 属绕行，不是规格落地。

## 变更内容

| 位置 | 变更 |
| --- | --- |
| §3 意图清单 | 补入 `payee_add`（位置与 `classifier.INTENT_LABELS` 一致），使「逐字抄录」成立 |
| §5 | 新增 SPEC-CHANGE 补记：`payee_add` → **L1**，**不发确认卡、不要 OTP**（口径「表单提交本身即用户确认」）；并明确 §5 表 L1 行的「会话内确认卡」**不适用于本意图** |
| `guard/permission.py` | `INTENT_BASE_TIERS` 登记 `"payee_add": "L1"` |
| `tests/test_spec_counts.py` | 新增守卫：§3 清单必须与 `classifier.INTENT_LABELS` **内容与顺序都一致**（与既有 §2 计数守卫同族） |
| `docs/features/README.md` | 更新那条「尚未列出 `payee_add`」的差异说明 |

## 兼容影响

- 不改任何工具函数签名、返回字段、数据库结构与权限判定阈值；
- **不新增意图标签**（`payee_add` 早已在 `classifier.INTENT_LABELS` 中），故 LLM 输出契约与 `IntentOut` 校验行为不变；
- **未改变任何用户可见行为**：`agent/payee_flow.py` 本就显式传 `tier="L1"`，登记后同一意图仍判 L1；唯一变化是**未传 `tier` 时不再落到 fail-closed L3**，档位来源标记由 `fail_closed` 变为 `spec_§5_table`（审计可溯）。

## 审批

用户（仓库 owner）已确认授权本次修改冻结规格 §3 与 §5。
