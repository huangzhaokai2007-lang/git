VERDICT: PASS
CARDS: card-17b
REVIEWED_DIFF: 已提交 258b5c2（7 文件），本审为提交后复核。agent/channel.py（新，29：通道入口薄函数 wrap_untrusted 纯转发 guard.injection）、agent/period.py（新，77：相对时间归一化唯一一份口径）、agent/orchestrator.py（343→285：import 后**再导出** period 三函数）、agent/classifier.py（1 行注释：`orchestrator.resolve_period`→`agent/period.resolve_period`）、interfaces/im/channel.py（215→219：改 import agent.channel）、tests/test_web_layering.py（+27：钉 IM 包裹入口）、tests/test_period_normalization.py（新，161）。基线 970=926+44。
CHECKED:
  - 接口一致性：通过。`orchestrator.resolve_period/resolve_day/anchor_period` 仍是**同一份实现**（再导出，非包装）；`interfaces/im/channel.py` 的 `WRAP` 现在指向 `agent.channel.wrap_untrusted`，签名与 `guard.injection.wrap_untrusted(source, text)` 一致；period 槽位口径：**完全没给** → 锚点当月（卡 09 未变）、**给了却认不出** → 判缺失 → CLARIFY（不静默换当月）。
  - 越界改动：2 个「范围外」文件已由 analyst 接受，我复核方向正当：`interfaces/im/channel.py` 的 3 行 import 是本卡正文要求（换掉命名空间借取）；`agent/classifier.py` 只有 1 行注释改引用路径（无行为变化）。日期槽位顺带修好（date_from/date_to 共用同一张别名表）。
  - 测试真实性（**3 个变异全真报警**，均已还原）：① 把 `interfaces/im/channel.py` 的 `WRAP` 退回 `orchestrator.injection.wrap_untrusted` → 16c 新守卫 `test_im_channel_wraps_through_the_agent_seam` FAIL（AST 抓到 `borrows=['orchestrator']`）；② 把 `agent/period.resolve_period` 的「认不出」改回回落当月 → tests/test_period_normalization.py **12 failed**（含 `never_guesses_the_anchor_month` 全参数、E2E `asks_instead_of_answering`、元用例 `meta_clearing_the_alias_table…`）；③ 把 orchestrator 的再导出改成包装函数 → `test_orchestrator_re_exports_the_same_implementation` FAIL。
  - 边界与异常：**E2E 回归主用例** `test_last_month_from_the_model_answers_august_not_the_anchor_month` **1 passed**——模型给 `last_month` → 回执里是 `2026-08` 且金额来自 `query.analyze_spending(shift(-1)).facts`（**不写死数字**，独立口径）、`ANCHOR not in reply`、解析月份也进审计；`test_unknown_relative_period_asks_instead_of_answering` 断言 states 走 CLARIFY、`tool_calls==[]`、追问里**不出现任何金额**；`test_missing_period_still_defaults_to_the_anchor_month` 保卡 09 行为。别名表覆盖中/英/下划线/连字符/大小写变体（last_month/Last Month/last-month/previous_month/…）。
  - 权限/审计/安全：`agent/channel.py` 是**纯转发**（不复制 guard 实现，`guard/injection.wrap_untrusted` 仍是唯一一份）；依赖成 `interfaces → agent → guard` 单向链；`interface` 不再借编排层命名空间（16c 守卫 AST 钉死 + 反例 `borrows`）。
RISKS:
  1. `agent/period.py` `from data.seed import AS_OF` —— **agent/ → data/ 越层**（架构铁律：agent 只许调 guard/tools）。但这是**既存**模式（`agent/orchestrator.py` 早已 `from data import dao` + `from data.seed import AS_OF`，card-09 已记 RISK），本卡只是把同一依赖搬到新模块，未新增越层面。建议按 card-09 的 TODO 收口：AS_OF 走 tools/ 层访问器。
  2. `RELATIVE_PERIOD_ALIASES` 是**手工维护**的表：新相对说法（「近 3 个月」「上一季度」等）认不出 → CLARIFY 追问。比静默错月好，但用户体验是「被回问」而不是「答对」。建议 17c/后续按真机返回的头几个高频写法补齐。
  3. 16c 新守卫的 `borrows` 判据「任何 `<Name>.injection` 属性访问即红」略宽：若将来别处有正当的 `<x>.injection`（非掏护栏），会误伤。当前无此写法，风险低。
  4. `resolve_day(value, edge=...)` 不校验 `edge` 取值（非 `"start"` 一律取月末）；调用点只传 start/end，当前无碍，建议加个 `Literal`/断言。
MUST_FIX: 无
EVIDENCE:
  - `uv run pytest --tb=no` → **970 passed**；`bash scripts/verify.sh` → `全部通过 ✅`
  - E2E 回归：`-k answers_august` → 1 passed（回执含 2026-08、金额来自 2026-08 事实包、不含锚点当月）
  - 变异①（退回命名空间借取）→ 16c 守卫 FAIL（`borrows=['orchestrator']`）；②（认不出回落当月）→ 12 failed；③（再导出改包装）→ 身份断言 FAIL（均已还原）
  - `grep -rn "变异 M" agent/ data/ interfaces/ tests/` → 无残留
  - 分层：`interfaces/im/channel.py` 现 import `agent.channel`；`agent/channel.py` 纯转发 `guard.injection`
VERDICT_REASON: 三条红线全过——#1 agent 薄函数落地（interfaces→agent→guard 单向、16c 守卫 AST 钉死、变异真红）、#3 period 归一化（英文别名 + 认不出走 CLARIFY、E2E last_month→2026-08 回归真过、变异 12 红）、period 拆分再导出身份不变（`is` 断言钉死、变异真红），970 基线零回归，无 MUST_FIX；仅 agent→data 越层（既存）/别名表手工维护/守卫判据偏宽 3 条非阻塞 RISK。

注：审核期间检测到 **card-18（API 层）并发进行**（未跟踪 interfaces/api/{app,__main__}.py + Dockerfile/docker-compose.yml/.dockerignore + docs/03-运行与评测.md + scripts/{api_smoke,bootstrap,docker-entrypoint}.sh；已改 scripts/verify.sh），全量 pytest 970 passed。与 card-17b（已提交、970 绿）无关，请知悉。
