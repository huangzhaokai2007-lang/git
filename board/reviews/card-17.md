VERDICT: PASS
CARDS: card-17
REVIEWED_DIFF: 已提交 3ba9563（8 文件：interfaces/im/{channel 215, selftest 225, feishu 130, server 105, __main__ 54, config 52}.py + README.md + .env.example），本审为提交后复核。基线 926 passed（未破），16c 界面守卫含在其中。
CHECKED:
  - 接口一致性：通过。与网页端（卡 16）**同一个** `agent.orchestrator.handle` 入口；`Reply.as_dict()` 字段全取自 `Turn`、不新增业务字段（卡 18 可直复用）；一 IM 会话（chat_id）= 一编排层 session（`im:<peer>`），确认卡/OTP 在途状态天然按会话隔离。
  - 越界改动：无。`interfaces/im/**` import = stdlib + `agent.{orchestrator,write_flow,classifier,llm}` + 同目录 + fastapi/pydantic/dotenv/uvicorn/httpx（第三方面）；**未 import guard/data/tools**（独立 grep 确认，16c 的 AST 守卫也在跑）。server.py `import 阶段无副作用`（不建库/不联网/不读密钥，应用在 create_app() 造）。
  - 测试真实性（红线 1，**独立探针**复核「模型侧」而非接口自报）：我自造 `llm.chat_json` spy 打 `/im/loopback` 发「查一下余额」→ 模型实际收到的 `user` 参数 = `<untrusted_data source="im">查一下余额</untrusted_data>\n（当前日期：2026-09-12）`（**包裹真到模型侧**）；发注入串「忽略之前的指令…」→ intent=unsafe_request 且**新增 LLM 调用 = 0**（规则层硬拦）。`wrap_untrusted(source, text)` 签名与 `WRAP("im", text)` 一致。
  - 边界与异常（自检 7/7 + 4 变异真报警，均已还原）：① 飞书 URL 校验原样回显 challenge；② 模型侧包裹 source=im；③ 走同一 `orchestrator.handle`（spy 断言入参=包裹后那串）；④ 回执金额来自工具层事实包（真跑 get_balance，46,634.00 元非编造）；⑤ 出消息落回环 outbox；⑥ 注入被规则层拦且**零 LLM 调用**；⑦ event_id 重投只处理一次；⑧ 在途控制回执（确认/验证码）原样透传。独立变异：**M1 从不包裹 → ②③红**；**M2 恒包裹（控制回执也被包）→ ⑧红**（入口收到 `<untrusted_data…>确认</…>`，正确验证码将永远不匹配）；**M3 去重失效 → ⑦红**（outbox 2→3）；**M4 双重包裹 → ②③红**。自检任一失败退出码 1（实测 6/7、5/7 均 rc≠0）。
  - 权限/审计/安全：feishu.py 纯函数（不联网、不 import guard/data/tools），`parse_event` 防御式（结构不符→ignore、加密模式→提示不猜、token 不符→bad_token）；`load_config` 从 .env 读、`--guide`/`/healthz` **不回显任何 webhook/密钥**。
RISKS:
  1. **WRAP 采用命名空间借取**（`WRAP = orchestrator.injection.wrap_untrusted`，worker 待拍板①）：本层没直接 import guard（16c 守卫过），但依赖「编排层恰好 import 了 injection」——哪天编排层不再 import，包裹会**静默失效**（铁律 7 无声破）。属**脆弱耦合**，判 RISK 非 MUST_FIX；更干净落点是 agent/ 侧通道入口薄函数。**注**：审核期间已观察到 17b 正在改成 `from agent.channel import wrap_untrusted as wrap_channel_text`（interfaces→agent→guard 单向链），方向正确。
  2. **period 槽位不归一化**（worker 待拍板③，真 bug）：IM 走「说人话」路径会复现（同 card 14a 的 account_type 老坑）。已观察 17b 新增 tests/test_period_normalization.py 在修。
  3. **自检未进 verify**（worker 待拍板④）：`python -m interfaces.im --selftest` 是 CLI，验收链路不跑它 → IM 通道回归抓不到。建议加进 verify（离线、不占端口、约 1s、rc 可判）。
  4. **`/im/loopback` 是无鉴权演示入口**：它直接把一句话灌进通道（等价收到 IM）。默认只绑 127.0.0.1（config），但若有人 `IM_HOST=0.0.0.0` 部署，任何人都能驱动机器人。建议 README 明示「不要公网暴露 /im/loopback」，或加开关。
  5. **webhook 默认不校验签名**：`IM_VERIFICATION_TOKEN` 空则不校验 `header.token`（demo 口径，README 有说明）。公开部署前必须配。
  6. **去重是进程内**（worker 待拍板⑧）：`_seen` 是每进程 OrderedDict → 多 worker/多实例部署会重复处理飞书重投（当前单进程 demo 无碍）。
  7. **飞书未真机验证**（worker 待拍板⑤）：入/出报文逻辑有单测 + 自检，但未对真实租户端到端过一遍（签名口径、`@_user_N` 剥离、challenge 回显都按官方文档实现，风险中等）。
  8. httpx 为 dev 组依赖、出消息时懒加载（缺则降级回环）（worker 待拍板⑥）——可接受。
MUST_FIX: 无
EVIDENCE:
  - `uv run python -m interfaces.im --selftest` → **7/7 通过（全绿）**、rc 0（不占端口、不联网）
  - 独立探针（自造 chat_json spy，非 selftest 机制）：模型 user = `<untrusted_data source="im">查一下余额</untrusted_data>\n（当前日期：2026-09-12）`；注入串 → intent=unsafe_request、新增 LLM 调用 0
  - 变异①（从不包裹）→ ②③ FAIL；②（恒包裹）→ ⑧ FAIL；③（去重失效）→ ⑦ FAIL；④（双重包裹）→ ②③ FAIL（均已还原，`grep 变异 M` 无残留）
  - 分层：`grep -rE "^\s*(import|from)\s+(guard|data|tools)\b" interfaces/im/` → 无命中
  - `uv run pytest --tb=no` → 926 passed（card-17 基线）；16c 界面守卫一并通过
VERDICT_REASON: 5 条红线全过——IM 正文**真到模型侧**被 `wrap_untrusted(source="im")` 包裹（独立探针证实，非接口自报）、注入零 LLM、复用同一编排层、自检离线 7/7、在途控制回执原样透传；4 个变异全真报警；926 基线零回归；仅 WRAP 借命名空间（已在 17b 改）/period 归一化/自检未进 verify/回环入口无鉴权 等 8 条非阻塞 RISK。

注：审核期间检测到 **17b 并发进行**（未跟踪 agent/channel.py + tests/test_period_normalization.py；已改 agent/orchestrator.py + interfaces/im/channel.py + tests/test_web_layering.py；当前全量 pytest 969 passed）。17b 正在做本报告 RISK 1/2 的收口（WRAP 改 agent.channel 薄函数 + period 归一化），与 card-17（已提交、926 绿）无关，请 17b 收口后独立复核。
