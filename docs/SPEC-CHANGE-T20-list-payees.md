# SPEC-CHANGE · 新增 T20 `list_payees`（查看白名单）

**变更类型**：`docs/01-接口规格.md` §2 工具契约**新增一个只读工具**（19 → 20 个）+ §3 意图清单**新增一个意图**（`payee_list`）。
**提出人 / 批准**：项目负责人（板块负责人）于实施前明确要求「新增一个查看白名单的功能，可以看到白名单与非白名单人员的手机号」，并**确认只做这一项**（另两项请求——非白名单转账改卡号+密码、新增收款人加验证码——**已明确不做**，理由见文末）。
**变更执行**：随实现一并提交。

---

## 1. 改了什么

| 位置 | 改动 |
|---|---|
| `docs/01-接口规格.md` §2 标题 | `（19 个，…）` → `（20 个，…）` |
| `docs/01-接口规格.md` §2 表 | 在 T19 之后**追加** T20 行 |
| `docs/01-接口规格.md` §3 | 意图清单在 `payee_add` 之后插入 `payee_list` |
| `tools/payee.py` | 实现 `list_payees()` |
| `agent/classifier.py` | `INTENT_LABELS` + `IntentLabel` + `SLOT_SCHEMA` 三处同步（另有 assert 自动校验） |
| `agent/read_routes.py` | `TOOL_ROUTES` 加 `payee_list` 路由 |
| `agent/orchestrator.py` | `READ_INTENTS` 加 `payee_list` |
| `agent/render_lists.py` | `payee_row` / `render_payees`（**精简表格**渲染） |
| `agent/templates.py` | `render()` 分发加分支 + 再导出 |

T20 契约：

```
T20  list_payees()
     入参：无
     data 关键字段：items[{payee_id, name, masked_phone, bank, is_whitelist}],
                    total_count, whitelist_count
     权限档：L0（只读）
     语义：列出**当前 user** 的全部收款人 + 白名单标记
```

**既有 19 个工具的名字、签名与返回字段一字未动**，本次是**纯追加**。

---

## 2. 为什么必须新增工具（而不是让编排层查库）

- `interfaces/` **只能**调 `agent/`，`agent/` **禁止直接写 SQL**（`CLAUDE.md` 架构红线）—— 所以"列个表"也必须有工具层出口；
- 现有 T6 `resolve_payee(query)` **只能按关键词查**，且要做歧义判定；`dao.find_payee` 的**空关键词会抛 `ValueError`**、也**不做归属过滤** —— 都不能拿来"列全部"。

---

## 3. 兼容性影响

| 维度 | 结论 |
|---|---|
| 既有 19 个工具 | **零影响**（纯追加，无重命名、无字段增删） |
| 数据库结构 | **零影响**（T20 只读，不建表、不写任何表） |
| 既有只读意图 | **零影响**（新路由只对 `payee_list` 生效） |
| `tests/test_spec_counts.py` | §2 标题数与表行数须同时改（已同步：20 == 20 行，序号连续）；§3 与 `classifier.INTENT_LABELS` 须内容与顺序一致（已同步） |
| `tests/test_orchestrator_readonly.py` | 有一条**锚定断言**钉着只读意图清单（卡 09 的 8 个）。本次是**契约扩展**，故同步更新该断言（原断言见 git 历史）—— **同 F10 接通 `assess_risk` 时的先例**；并把名不副实的测试名 `..._the_eight_from_the_card` 一并纠正为 `..._the_contracted_set` |

---

## 4. 编号说明（第三次冲突，本次一并记录）

- **T19 已被 F20 的 `preview_gift` 占用**；本变更占用 **T20**。
- 仍**未落地**的提案：F18 的 `remind_subscription`（原申请 T19）、F16 的落预约/结算/取消（需 3 个编号）。
- 故 **F18/F16 的提案应从 T21 起**，**待队长一次性裁决**（已在 T20 行备注与本文件同时标出）。

---

## 5. 明确**不做**的两项（本次请求里被否掉的）

| 请求 | 结论与理由 |
|---|---|
| 非白名单转账改为「输入银行卡号 + 密码」 | **不做**。① 项目**已有明确禁令**：`tools/card_binding.py:172` 写着「请通过绑定银行卡表单输入卡号和密码，**不要在聊天中发送凭证**」，而聊天是本项目的主通道；② `guard/injection.py` 把「绕过密码验证」列为**注入攻击模式**；③ 金融场景里让用户在对话中输入银行卡密码是安全反模式，会直接冲击已有加分项（注入防御、权限分级）。若确要做，须限定为**表单输入**（照 `card_binding_ui` 的 `SecretStr` + 用后即清），且另开 `SPEC-CHANGE` 审批 |
| 新增收款人加「验证码」 | **不做**。现状（F22 已批准的口径）是 `payee_add` = **L1，不发确认卡、不要 OTP**，理由是「**表单提交本身即用户确认**」——只加联系人、不动钱。加 OTP 是推翻已批准口径且给用户添麻烦 |

---

## 6. 通知全员（规格开头第 3–4 行的要求）

- [ ] 团队频道公布：§2 标题 19 → 20、新增 T20 `list_payees`；§3 新增意图 `payee_list`
- [ ] 提醒：F18 / F16 的编号提案需从 **T21** 起，请队长裁决
- [ ] 提醒本周集成人员：`agent/classifier.py`、`agent/read_routes.py`、`agent/orchestrator.py`、`agent/templates.py` 由本变更修改（均为**纯追加**）

---

## 7. 剩余限制（如实登记）

- **`data/dao.py` 已正好 300 行**（单文件红线），加不下 `list_payees` 原语，故 `tools/payee.py` **直查 SQL**（仓内先例：`tools/cross_scene.py:_savings_row`）。**补 DAO 原语并拆 `dao.py` 属另一张卡。**
- **`Turn.tier` 对只读意图恒为 `None`**（`ctx.tier` 只在写路径与 payee/risk 分支被赋值），档位只在审计行的 `permission_tier` 上可见 —— 这是**既有**的不对称（`risk_assess` 例外），本次未改动共享只读路径，仅如实登记。

---

## 8. 验证

```bash
uv run pytest tests/test_payee_whitelist.py -v    # 新增 5 条
uv run pytest tests/test_cases.py -v              # 用例通过率 47/47
uv run pytest tests/test_spec_counts.py -v        # 规格计数与 §3 守卫
uv run pytest tests/test_orchestrator_readonly.py -v
bash scripts/verify.sh
```

真实模型冒烟：

```bash
uv run python -m app.cli "我的白名单有哪些"
# → 您共有 5 位收款人：白名单 2 位、其它 3 位。
#
#    | 收款人 | 手机号 | 银行 | 白名单 |
#    | --- | --- | --- | --- |
#    | 李四 | 139****1001 | 招商银行 | ✅ |
#    | 王五 | 137****2003 | 建设银行 | ✅ |
#    | 张小美 | 136****3004 | 中国银行 | — |
#    | 李四 | 139****1002 | 工商银行 | — |
#    | 赵六 | 135****4005 | 农业银行 | — |
#
#    白名单：转账只需会话内确认；非白名单：转账需确认卡 + 短信验证码。
```

**排版口径（用户后续要求）**：回执从「【分组】+ 项目符号」改为**精简 Markdown 表格**
（`收款人 / 手机号 / 银行 / 白名单` 四列，一行一位收款人），分组语义由「白名单」列
（`✅` / `—`）+ 工具层既有排序（`is_whitelist DESC, name, id`）表达，**行序一个字没重排**；
表下只留**一行**说明两组的转账验证差别。`bank` 为空时该格印 `—` 占位（**不是**编造的银行名）。
