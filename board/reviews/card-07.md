VERDICT: PASS
CARDS: card-07
REVIEWED_DIFF: 8 个文件 —— tools/wealth.py(+205)、tools/cross_scene.py(+161)、tools/_wealth_risk.py(+208) 未跟踪新文件；tests/test_tools_wealth_risk.py(+211)、tests/test_tools_wealth_trade.py(+183)、tests/test_tools_wealth_redeem.py(+167)、tests/test_tools_cross_scene.py(+153) 未跟踪新文件；tests/conftest.py(修改，加 _wealth_risk._ASSESSMENTS 逐用例复位)。全部 ≤300 行。
CHECKED:
  - 接口一致性：通过。T13 data={risk_level,valid_until}、T14 data=items[{product_id,name,risk_level,yield,term}]（5 字段冻结）、T15 data={order_id,product_name,amount,expected_confirm_date}、T16 data={plan_id,lock_id,items,total} 与规格 §2 逐字对齐；`yield` 用 `Field(alias="yield")` 正确处理关键字；ErrorCode 复用 schemas，未新增取值。
  - 越界改动：无。范围只 8 个文件；confirm_ref 复用 card-06 的 subscription 机制（import 不复制，测试钉死 `check_confirm_ref is sub.check_confirm_ref`）；_ASSESSMENTS 私有存储复位已入 conftest。
  - 测试真实性（变异抽查 3/3 真报警，均已还原）：① T14 风险上限过滤 `_rank(...) <= ceiling` 改成 `if True` → test_t14_never_recommends_above_the_user_level[R1/R2/R3] 3 条 FAIL；② T15 风险匹配 `_rank(product) > _rank(assessment)` 改成 `if False` → test_t15_low_risk_user_cannot_buy_higher_risk 5 条 FAIL；③ T16 锁资金把 `available` 改成 `balance` → test_t16_locks_funds_without_spending 1 条 FAIL。均还原、指纹恢复、649 passed 复跑绿。
  - 边界与异常：通过。未成年人(<18)→FORBIDDEN+rejected 审计且不落测评结果；未测评/过期测评→INVALID_STATE（T14/T15 都钉住）；风险越界过滤逐档钉死完整清单；买入校验顺序（测评→风险匹配→起购额→归属→余额）正确；赎回（持仓存在→封闭期→金额→归属）正确；金额整数分禁浮点；幂等（同 ref 重放只执行一次、新 ref=新笔）；原子性（monkeypatch insert_audit 抛错→扣款+持仓/锁资金整体回滚）。
  - 权限/审计/安全：通过。T15 越权账户 fail-closed FORBIDDEN+rejected；每个成功写恰一条审计（intent=wealth_buy/wealth_redeem/gift_plan、tier=L2）；FORBIDDEN 按 reviewer 口径写 rejected；T16 空库 fail-closed FORBIDDEN 且零残留；OTP 明文不入库；L0 工具（T13/T14）不写审计；金额整数分全程无 float（yield 是费率不是金额，格式化百分数字符串不引入浮点）。
RISKS:
  1. tools 层直接写 SQL（架构第④层越界苗头）：wealth.py 的 `_holdings`/`_write_holding` 与 cross_scene.py 的 `_lock_funds` 用 `dao.connection()` + 裸 SQL 直改 holding/account 表，绕过 DAO 业务函数。影响：DAO 缺 list_holdings/insert_holding/lock_funds 原语（补它们超卡 07 范围），作者已 TODO(07b)；SQL 均参数化无注入、按 user_id 过滤无越权，但属分层违反，评审可能按「tools 只能调 DAO」扣分。建议：07b 优先补 DAO 原语，替掉这三处直 SQL。
  2. `_score` 年龄 ≥200 未处理：AGE_BANDS 最后一段 (66,200)，`next(score for ... if low <= age < high)` 在 age≥200 时抛未捕获的 StopIteration（assess_risk 只 except ToolError），会冒泡成未处理异常而非干净的 ToolResult。影响：输入荒谬岁数（≥200）才会触发，演示不踩；但属未处理异常路径。建议：AGE_BANDS 末段改 (66, 999, 5) 或给 next 加默认值。
  3. T14 签名放宽：规格 §2 写 `recommend_wealth(risk_level,horizon_days,amount)`（无默认），实现把三者都改成可选（risk_level 缺省取本会话测评、horizon/amount 缺省不过滤）。影响：多接受缺省形态，不破坏既有调用，但属规格未标注的接口放宽。建议：若不认可，07b 收窄为规格签名或走 SPEC-CHANGE 注明。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no -p no:cacheprovider` → 649 passed（独立复跑，非引用）
  - `bash scripts/verify.sh` → 649 passed、3/5–5/5 SKIP（卡11/09/12-13）、「全部通过 ✅」
  - `git status --short` → M tests/conftest.py + 7 个 ??（3 源文件 + 4 测试），共 8 文件，范围干净
  - 变异①（T14 上限过滤→if True）→ 3 FAIL（已还原）；变异②（T15 风险匹配→if False）→ 5 FAIL（已还原）；变异③（T16 available→balance）→ 1 FAIL（已还原）
  - 还原后复跑 649 passed；`_wealth_risk.py:122` 越界校验已确认是 `if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<len(rule)`（worker 之前残留的 `if False:` 已修好）
VERDICT_REASON: 8 文件范围干净、649 测试稳定全绿、T13 纯代码计分/T14 风险越界过滤/T15 风险匹配+确认凭证+事务/T16 锁资金+mock 预订全部落实且变异抽查证伪有效，无 MUST_FIX；仅 3 条非阻塞 RISK（tools 直写 SQL、年龄≥200 未处理、T14 签名放宽）。
