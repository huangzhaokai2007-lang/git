VERDICT: PASS
CARDS: card-14a
REVIEWED_DIFF: 4 个文件（已提交 7361c99，本审为提交后复核）—— guard/tool_guard.py（+114，统一入口校验）、tools/_query_common.py（require_owned 改转发）、tools/transfer.py（preview_transfer 接入金额边界）、tests/test_tool_guard.py（+123，21 用例）。基线 852=831+21 零回归。
CHECKED:
  - 接口一致性：通过。require_owned 语义与卡 06/07 一致（非本人 → FORBIDDEN、文案「{resource}不属于当前用户」、不泄露对方 user id）；require_amount_cents 正整数分（0/负/浮点/bool/字符串→INVALID_ARGUMENT，超 5,000,000→OVER_LIMIT）；require_payee_exists 不存在→NOT_FOUND；错误码全在冻结枚举（FORBIDDEN/INVALID_ARGUMENT/OVER_LIMIT/NOT_FOUND 已在 ErrorCode）。
  - 越界改动：无。guard/tool_guard 用函数内延迟导入 tools._query_common（避免 tools→guard→tools 模块级循环）；tools 侧 require_owned 改为转发到 guard（单一实现，test_query_common_require_owned_is_a_forwarder 钉住）。
  - 测试真实性（变异抽查 3/3 真报警，均已还原）：① require_owned 的越权分支加 `False and` → test_foreign_resource_is_forbidden ×4 + foreign_owner_none + rejection_writes 共 6 条 FAIL；② require_amount_cents 上界 `cents > AMOUNT_HARD_MAX_CENTS` 加 `False and` → test_illegal_amounts_are_rejected[5000001-OVER_LIMIT] 1 条 FAIL；③ 正整数分支加 `False and` → test_illegal_amounts_are_rejected[0/-1/10.5/True/"100"/None] 6 条 FAIL。还原后恢复（grep 无 `False and` 残留）。
  - 边界与异常：通过。越权四类资源（账户/卡/持仓/订阅）fail-closed + 写 audit_log.result='rejected'（tool/trace_id 可溯）+ risk_event；owner=None 也拒（不「查不到就放行」）；金额 0/负/浮点/bool/字符串全拦、硬上限 5,000,000 分 = 50,000 元；集成断言 preview_transfer 超上限被拦且 data/facts 空。
  - 权限/审计/安全：通过。越权同时留两笔痕（audit_log 业务流程视角 + risk_event 安全视角，语义不同缺一不可）；审计/risk_event 写失败只告警不吞掉拒绝。
RISKS:
  1. `require_payee_exists` 用 `dao.find_payee`（姓名/手机号/银行**模糊子串**检索）而非 id 精确查找：有效收款人 id「payee_0001」会被误判 NOT_FOUND（find_payee 不搜 id 列），函数名/docstring 说「id 必须存在」但实现是「名字/手机号任一可检索到」。当前测试只覆盖名字（「王五」）与垃圾串，没覆盖真实 id。若未来给 preview_transfer 接线（传 payee_id），会系统性误拒。
  2. 金额两层上限并存：tool_guard 硬上限 5,000,000 分（50,000 元）vs transfer.py 既有单笔上限 50,000 分（500 元）口径不一致。§5 硬约束「单笔上限 5 万分/笔」，transfer 的 50k 分是对的，tool_guard 的 5M 分是另一档（更宽），需统一口径（worker 待拍板④）。
  3. `UNAUTHORIZED_FACTOR="unauthorized_resource"` 超出 risk_event.factor 的 DDL CHECK 枚举（night/geo/device/velocity/amount_jump/new_payee）——写 risk_event 会被 DDL 拒绝。需 SPEC-CHANGE 加枚举值（worker 待拍板①，已记 14b）。
  4. require_owned 的 tool 名传播未完成：39 个老调用点（卡 06/07 起）走不带 tool 的转发，audit 的 tool 字段暂缺；14b 机械收尾（worker 待拍板②）。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 852 passed（独立复跑，非引用；后续 14b 并发改动致工作区 7 失败，见下）
  - `bash scripts/verify.sh` → 852 passed、第 3 段 32 passed、第 5 段 84 passed、`全部通过 ✅`
  - 变异①（越权分支 if False）→ 6 FAIL（已还原）；变异②（上界 if False）→ 1 FAIL（已还原）；变异③（正整数分支 if False）→ 6 FAIL（已还原）
  - 还原后 grep 确认无 `False and` 残留
VERDICT_REASON: 越权 fail-closed 统一收口（audit+risk_event 双痕）、金额正整数分与硬上限、收款人存在性三件套落实，3 次变异抽查证伪有效，852 基线零回归，无 MUST_FIX；仅 require_payee_exists 语义、金额双上限、factor 枚举、tool 名传播 4 条非阻塞 RISK。

注：审核过程中检测到 14b 并发改动（data/schema.sql + data/dao.py + data/_dao_core.py + guard/tool_guard.py + tests/test_tool_guard.py 被改，工作区现 7 个失败——test_db.py 的「schema.sql 与规格 DDL 逐字一致」被打破，因 14b 往 risk_event.factor CHECK 枚举加了 unauthorized_resource 但规格 DDL 未同步）。此为 14b 的 SPEC-CHANGE 阻塞，不影响本卡（card-14a 已提交且 852 绿）的 PASS 结论，但需 analyst 知悉。
