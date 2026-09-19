"""IM 通道 HTTP 服务（卡 17）：飞书事件订阅入口 + 无网可用的本地回环 demo 通道。

路由：

======================  ====================================================
`POST /im/webhook`      飞书开放平台「事件订阅」回调（URL 校验 / `im.message.receive_v1`）
`POST /im/loopback`     无网 demo：把一句 IM 正文灌进通道（等价于"收到一条 IM"），直接返回回执
`GET  /im/outbox`       轮询"机器人本该发出去的消息"（回环出消息的口径）
`GET  /healthz`         存活 + 当前出消息方式
======================  ====================================================

并发与分层：

- 路由是**同步函数** → FastAPI 把它丢进线程池；`data/` 已按线程各持一份连接 + `BEGIN IMMEDIATE`
  （卡 16b），所以多线程并发请求安全；
- 本层只 import `agent/` 与同目录组件（`tests/test_web_layering.py` 有 AST 守卫）；
- **import 阶段无副作用**：不建库、不联网、不读密钥 —— 配置与应用都在 `create_app()` 里造
  （与 `agent/llm.py` 同一条纪律，保证"没配 key 也能起"）；
- 每条路由只负责"注册 + 转调"，业务处理在 `_*_body()` 里（可单独 import 来测）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Body, FastAPI
from pydantic import BaseModel, ConfigDict

from interfaces.im import feishu
from interfaces.im.channel import SOURCE_IM, Channel
from interfaces.im.config import ImConfig, load_config

logger = logging.getLogger(__name__)

#: 忽略类事件（加密 / token 不符 / 其它事件类型）统一由这里收口
IGNORED_KINDS = ("encrypted", "bad_token", "ignore")


class LoopbackIn(BaseModel):
    """本地回环请求体：一句话 + 可选会话标识（同一 `peer_id` 共享确认卡/OTP 在途状态）。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    peer_id: str = "local-demo"


def _webhook_body(payload: dict, channel: Channel, cfg: ImConfig) -> dict[str, Any]:
    """飞书事件订阅回调：URL 校验回显 challenge；消息事件走通道（其余事件一律忽略）。"""
    event = feishu.parse_event(payload, verification_token=cfg.verification_token)
    if event.kind == "challenge":
        return feishu.challenge_echo(payload)
    if event.kind in IGNORED_KINDS:
        note = event.note or "已忽略"
        logger.info("IM webhook 未处理：%s", note)
        return {"code": 0, "msg": note, "handled": False}
    if not event.text:
        return {"code": 0, "msg": "非文本消息（demo 只处理文本）", "handled": False}
    if not channel.first_time(event.event_id):
        return {"code": 0, "msg": "重复事件已忽略（飞书重投）", "handled": False}
    reply = channel.handle(event.text, peer_id=event.peer_id)
    return {"code": 0, "msg": "ok", "handled": True, **reply.as_dict()}


def _loopback_body(body: LoopbackIn, channel: Channel) -> dict[str, Any]:
    """无网演示入口：直接灌一句 IM 正文（等价于"用户给机器人发了这句话"）。"""
    reply = channel.handle(body.text, peer_id=body.peer_id)
    return {"code": 0, "msg": "ok", "handled": True, **reply.as_dict()}


def _outbox_body(channel: Channel) -> dict[str, Any]:
    """回环出消息：真实环境里这些文本会被机器人发到 IM 会话中。"""
    items = channel.outbox()
    return {"count": len(items), "items": items}


def _health_body(cfg: ImConfig, version: str) -> dict[str, Any]:
    """存活探针：只报"用哪种出消息方式"，**不回显任何 webhook / 密钥**。"""
    return {"status": "ok", "channel": SOURCE_IM, "outbound": cfg.outbound, "version": version}


def create_app(config: ImConfig | None = None) -> FastAPI:
    """构造 FastAPI 应用（`config` 为空时读 `.env` + 进程环境）。"""
    cfg = config or load_config()
    channel = Channel(cfg)
    app = FastAPI(title="AI Banking Agent · IM 通道（模拟环境 / 合成数据）", version="0.1.0")

    @app.post("/im/webhook")
    def webhook(payload: dict = Body(...)) -> dict[str, Any]:
        return _webhook_body(payload, channel, cfg)

    @app.post("/im/loopback")
    def loopback(body: LoopbackIn) -> dict[str, Any]:
        return _loopback_body(body, channel)

    @app.get("/im/outbox")
    def outbox() -> dict[str, Any]:
        return _outbox_body(channel)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return _health_body(cfg, app.version)

    return app
