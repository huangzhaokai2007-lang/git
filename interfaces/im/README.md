# IM 通道（卡 17）

把一个 IM 会话接到**同一个编排层**上：收到消息 → `wrap_untrusted(source="im")` 包裹 → `agent.orchestrator.handle`
→ 回执发回去。IM 层不含任何业务逻辑（不判意图、不判权限、不算数字），与网页端（卡 16）共用一份编排层。

## 1. 无网 30 秒演示（回环通道，不需要任何配置）

```bash
uv run python -m data.seed --reset              # 造合成数据（首次）
uv run python -m interfaces.im                  # 起服务，默认 http://127.0.0.1:8090

# 另开一个终端：把一句话当成"用户发来的 IM 正文"灌进通道
curl -s -X POST http://127.0.0.1:8090/im/loopback \
  -H 'content-type: application/json' \
  -d '{"text": "查一下余额"}'
# → {"code":0,"msg":"ok","handled":true,"trace_id":"trace-…","intent":"balance_query",
#    "tool_calls":["get_balance"],"tier":null,"executed":false,"pending_id":null,
#    "reply":"您的储蓄账户余额为 … 元，可用余额 … 元（截至 …）。"}

curl -s http://127.0.0.1:8090/im/outbox         # 轮询"机器人本该发出去"的消息
```

多轮写操作也可以走同一条通道（同一个 `peer_id` = 同一个会话，确认卡/OTP 状态按会话隔离）：

```bash
curl -s -X POST http://127.0.0.1:8090/im/loopback -H 'content-type: application/json' \
  -d '{"text": "给王五转100元", "peer_id": "demo-1"}'      # → 转账确认卡（文本）
curl -s -X POST http://127.0.0.1:8090/im/loopback -H 'content-type: application/json' \
  -d '{"text": "确认", "peer_id": "demo-1"}'              # → 要验证码
curl -s -X POST http://127.0.0.1:8090/im/loopback -H 'content-type: application/json' \
  -d '{"text": "123456", "peer_id": "demo-1"}'            # → 执行回执（金额来自工具层事实包）
```

验证码由**工具层**比对（`tools/transfer.py`，规格 §5 demo 固定 6 位）；IM 层不知道也不需要知道它。
多轮里的"确认/验证码"这类**控制回执**是**原样**送进编排层的（不包裹）—— 它们不会进模型，
而验证码要与工具层逐字相等才通过，包起来会让正确验证码永远匹配不上（详见 `channel.payload_for()`）。

## 2. 离线自检（不联网、不占端口、不需要 LLM key）

```bash
uv run python -m interfaces.im --selftest
```

用 stdlib `asyncio` 直接按 ASGI 协议驱动应用，把 LLM 与编排层入口换成探针，逐条断言：
URL 校验回显 / **模型上下文里确实是 `<untrusted_data source="im">…</untrusted_data>`** /
走同一份 `orchestrator.handle` / 回执数字来自工具层事实包 / 出消息落进回环 outbox /
注入正文被规则层拦下且**零 LLM 调用** / 同一 `event_id` 重投只处理一次。

## 3. 接飞书（需要公网可达地址）

**出消息** —— 群自定义机器人：

1. 飞书群 → 设置 → 群机器人 → 添加机器人 → **自定义机器人**，拿到 webhook 地址；
2. 若要开启「签名校验」，把密钥一起填进 `.env`；
3. 配好后 `/healthz` 里的 `outbound` 会变成 `feishu-bot`，回执会真的发到群里。

**入消息** —— 开放平台应用的事件订阅：

1. 开放平台 → 创建应用 → 事件订阅 → 请求地址填 `https://<你的公网地址>/im/webhook`；
2. 飞书会先发 `{"type":"url_verification"}` 做校验 —— 本服务按协议**原样回显** `challenge`；
3. 订阅事件 `im.message.receive_v1`；把开放平台的 Verification Token 填进 `IM_VERIFICATION_TOKEN`
   （填了就校验，不填 demo 不校验）；
4. 本地演示用内网穿透把 8090 暴露出去（示例：`ngrok http 8090`），再把公网地址填到开放平台。

请求地址保存成功后，在群里 @机器人 说「查一下余额」，回执会发回群里；
`/im/outbox` 里也能看到每一条本该发出去的消息（便于截图/排障）。

## 4. 环境变量（见仓库根 `.env.example`）

| 变量 | 说明 | 不配的后果 |
| --- | --- | --- |
| `IM_HOST` / `IM_PORT` | 监听地址/端口（默认 `127.0.0.1:8090`） | 用默认值 |
| `IM_BOT_WEBHOOK` | 飞书自定义机器人出消息 webhook | 只能走本地回环（`/im/outbox` 轮询） |
| `IM_BOT_SECRET` | 机器人「签名校验」密钥 | 出消息不带签名 |
| `IM_VERIFICATION_TOKEN` | 事件订阅 Verification Token | 不校验回调来源（仅 demo 可接受） |

## 5. 安全说明与已知限制

- **铁律 7**：IM 正文一律先 `wrap_untrusted(source="im")` 包裹再进编排层 —— 自定义机器人/群成员
  可能发任何内容，模型上下文里它始终是**数据**，不是指令；注入由 `guard/injection.py` 规则层
  **零 LLM 调用**拦下（自检 ⑥ 断言）。
- **入口规则**：会走模型的新请求一律包裹；确认卡/验证码这类**控制回执**（编排层按设计不调模型、
  且验证码要与工具层逐字相等）原样透传（自检 ⑧ 断言）。判定在途用的是编排层自己的
  `agent.write_flow.inflight(session_id)`，不是复制一份逻辑。
- **事件加密模式不支持**：飞书开启「加密」后回调体是 AES 密文，解它要引新依赖（铁律 6 禁止）。
  服务会明确回一句"请在后台关掉加密"，而不是静默失败。
- `IM_BOT_WEBHOOK` 是一条**可用即发**的凭据 → 只放 `.env`（不进 git），`/healthz` 与日志都不回显。
- 出消息失败（超时/HTTP 非 2xx/未装 httpx）→ **降级落在回环 outbox**，通道不会因此报错给用户。
- 事件去重：只记最近 512 个 `event_id`，进程重启后重置（demo 口径；飞书重投窗口远小于此）。
- 全合成数据、模拟环境，不连接任何真实银行。
