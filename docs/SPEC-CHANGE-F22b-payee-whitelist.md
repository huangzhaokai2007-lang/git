# SPEC-CHANGE · 收款人白名单（T25 + 意图 `payee_whitelist`）

**变更类型**：① §2 **新增一个工具**（24 → 25）；② §3 **新增一个意图**（28 → 29）；
③ §5 **新增一条档位**：`payee_whitelist` → **L2**。
**批准**：人类已批「按建议做（L2 + 验证码，不加输入手机号）」。本卡由用户直接提出需求。

---

## 1. 为什么必须 L2，而不是沿用 `payee_add` 的 L1

**白名单 = 转账时不做短信验证的特权。** 如果「加收款人」（L1、表单提交即生效）能顺带把对方
设成白名单，那么账户一旦被盗，攻击者只要把自己加进白名单，就能**永久绕过 OTP**。

所以「授予这个特权」必须单独验证身份 —— 这是本卡存在的全部理由。

⚠️ 同时纠正一处**过度承诺**（本卡自查发现）：白名单只把**基础档位**降到 L1；
```text
§5 的风险因子依然适用：night(23:00-06:00)、velocity(10min≥3 笔)…
实测证据（23:40）：
  加入前 张小美 L3  factors=['night','new_payee']
  加入后 张小美 L2  factors=['night']      ← 白名单去掉了 new_payee
          王五   L2  factors=['night']      ← 种子里的白名单收款人，深夜同样要 OTP
```
卡面文案因此改为「基础档位降为 L1；但风险因子仍会升档」，不再写「不再需要短信验证码」。

---

## 2. 改了什么

| 位置 | 改动 |
|---|---|
| `docs/01-接口规格.md` §2 | 标题 24 → 25，追加 T25 行 |
| `docs/01-接口规格.md` §3 | 意图清单插入 `payee_whitelist`（29 个） |
| `tools/payee.py` | **T25 `set_payee_whitelist(payee_id, enable, confirm_ref)`** + `issue_confirm_ref` / `check_confirm_ref` / `whitelist_target` |
| `agent/_write_payee.py` | 新模块：白名单族的 5 个纯函数（shape/prepare/card/plain/execute） |
| `agent/write_intents.py` | 注册 `payee_whitelist` 描述符（`confirm_ref` 族、`otp_in_tool=False`） |
| `agent/classifier.py` | `INTENT_LABELS` / `IntentLabel` / `SLOT_SCHEMA` 三处 + 触发说法（与 `payee_add` 划清边界） |
| `guard/permission.py` | `INTENT_BASE_TIERS["payee_whitelist"] = "L2"` |
| `agent/payee_flow.py` | `submit_payee(..., whitelist=False)`：加完人若勾了白名单，接着复用 `write_flow.start` 出 L2 卡 |
| `interfaces/web/components.py` | 收款人表单加复选框「同时加入白名单（之后转账免验证码，需再输一次验证码）」 |
| `interfaces/web/forms.py` | 把 `whitelist` 透传给 agent |

**既有 24 个工具的名字、签名与返回字段一字未动**；`payee_add` 仍是 L1。

---

## 3. 安全要点：凭证绑定方向

```python
target = f"{payee_id}:{int(enable)}"      # payee.whitelist_target()
```
拿「加入」的凭证去执行「移出」→ `FORBIDDEN`（已测）。TTL 300s、一次性消费、同一 ref 幂等。

其余：归属 fail-closed（他人的收款人 → `NOT_FOUND`，不泄露存在性）；`UPDATE` + 审计同一事务；
手机号只出现脱敏形式（铁律 8）。

---

## 4. 兼容性影响

| 维度 | 结论 |
|---|---|
| 既有 24 个工具 | 零影响（纯追加） |
| `payee_add` 的行为 | **不变**（仍 L1、表单即生效、不发确认卡） |
| 表单返回值 | `payee_form()` 从 `(name, phone)` 变成 `(name, phone, whitelist)` —— 只有 `forms.py` 一个调用点，已同步 |
| 意图数 | §3 28 → 29（`tests/test_classifier.py` 的独立抄录同步） |

---

## 5. 已知限制（如实登记）

- **白名单目前不可「移出」**：T25 支持 `enable=False`，但**没有对应意图**；聊天层只能加入。
- **没有「设置收款人白名单」的独立说法**：撤销需走工具层或后续补意图。
- 白名单页的措辞在 `agent/render_lists.py`（当前工作区有他人未提交的表格化改版），
  其中「非白名单」的说明写着「需要确认卡号」—— **本项目没有卡号输入环节**，该措辞不准确。

---

## 6. 通知全员

- [ ] §2 工具数 24 → 25（T25）；§3 意图数 28 → 29（`payee_whitelist`）
- [ ] 提醒：`payee_whitelist` 是 **L2 + OTP**，不要在别的模块里按 L1 处理它
- [ ] 提醒：白名单**不等于**「永远免验证码」，风险因子仍会升档（深夜/短时多笔）

---

## 7. 验证

```bash
uv run pytest tests/test_payee_whitelist_flow.py -v     # 5 条（含方向绑定、幂等、L2+OTP）
uv run pytest tests/test_classifier.py tests/test_spec_counts.py -v
bash scripts/verify.sh                                  # 1436 passed / 用例 49-49 / 冒烟 4-4
```
