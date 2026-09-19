"""IM 通道核心（卡 17）：收到一条 IM 消息 → 包裹 → **同一个编排层** → 把回执送回去。

三条铁律的落点：

- 铁律 3/7：IM 正文来自**第三方通道**，是不可信文本 → 进编排层前必须
  `wrap_untrusted(source="im")` 包裹，模型上下文里看到的是
  `<untrusted_data source="im">…</untrusted_data>`；**入口规则**见 `payload_for()`：
  会走模型的新请求一律包裹，而确认卡/验证码这类**控制回执**（编排层按设计不调模型、
  且验证码要与工具层逐字相等）原样透传 —— 实测包裹验证码会让正确验证码永远不匹配（见 `payload_for`）；
- 铁律 4（同一编排层）：包裹后的文本交给 `agent.orchestrator.handle` —— 与网页端（卡 16）
  **同一个入口**，IM 层不复制任何业务逻辑（不判意图、不判权限、不算数字）；
- 分层：本层只 import `agent/` 与同目录组件（`tests/test_web_layering.py` 有 AST 守卫），
  不碰 `tools/` `data/`，不写 SQL。

会话口径：一个 IM 会话（`chat_id`）＝ 一个编排层 session（`im:<peer>`），
于是确认卡 / OTP 的**在途状态天然按会话隔离**（`handle(session_id=…)`），
两个群同时转账也不会互相顶掉确认卡。

`wrap_untrusted` 的实现归 `guard/`，而 `interfaces/` 禁止 import `guard/`（CLAUDE.md 分层铁律 +
卡 16c 的机器守卫）—— 包裹的唯一入口是 `agent.channel.wrap_untrusted`（卡 17b 的通道入口薄函数），
它再转发 `guard.injection.wrap_untrusted`，于是依赖是干净的 `interfaces → agent → guard` 单向链，
也不必再"借"编排层的命名空间（卡 17 的 `orchestrator.injection.…` 写法脆弱：编排层哪天不再
import `injection` 就静默失效）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agent import orchestrator, write_flow
from agent.channel import wrap_untrusted as wrap_channel_text      # 通道入口（卡 17b）
from agent.orchestrator import Turn

from interfaces.im import feishu
from interfaces.im.config import ImConfig

logger = logging.getLogger(__name__)

#: 通道标记：包裹成 `<untrusted_data source="im">…</untrusted_data>`（卡 17 第 2 条）
SOURCE_IM = "im"
#: 去重表容量：飞书会重投同一事件，只记最近 N 个 event_id（超出丢最旧）
SEEN_LIMIT = 512
#: 回环 outbox 记忆条数（HTTP 轮询 demo 用）
OUTBOX_LIMIT = 100

#: 不可信文本包裹函数（铁律 7 的数据层）：**唯一入口**是 `agent.channel.wrap_untrusted`
#: （它再转发 `guard.injection.wrap_untrusted`）—— 本层不掏护栏层、也不借编排层的命名空间。
WRAP: Callable[[str, object], str] = wrap_channel_text


def session_for(peer_id: str) -> str:
    """IM 会话 → 编排层 session id（同一会话的确认卡/OTP 在途状态靠它隔离）。"""
    return f"{SOURCE_IM}:{peer_id or 'unknown'}"


def wrap_incoming(text: str) -> str:
    """把 IM 正文包成不可信数据块（铁律 7）：`source` 固定 `im`。"""
    return WRAP(SOURCE_IM, text)


def payload_for(text: str, *, session_id: str) -> str:
    """决定这一条 IM 消息**送进编排层的文本**（本层唯一的入口规则）。

    - 会话里**没有在途确认** → 这是一句新请求，会走模型（CLASSIFY）：IM 正文是不可信文本，
      按铁律 7 包裹（`source=im`）再进编排层；
    - 会话里**有在途确认**（确认卡 / 验证码阶段）→ 这条是**控制回执**（"确认" / 6 位验证码），
      编排层按设计**完全不调模型**（`write_flow.resume` 直接落工具层），而验证码要与
      `tools.transfer.OTP_CODE` **逐字相等**才通过 —— 包裹了会让正确验证码永远不匹配
      （实测：包成 `<untrusted_data …>123456</untrusted_data>` → "验证码不正确"）。
      规格 §6 的措辞也是"包裹后**再进模型上下文**"，故控制回执原样透传。

    在途判定用编排层自己的 `write_flow.inflight(session_id)`（**同一份实现**，不是复制一份逻辑）。
    """
    return text if write_flow.inflight(session_id) is not None else wrap_incoming(text)


@dataclass(frozen=True)
class Reply:
    """一次 IM 交互的结果（回给通道的报文用）。

    `payload` 是真正送进编排层的那串：新请求 = 已包裹（`source=im`）；在途控制回执 = 原样透传。
    """

    session_id: str
    payload: str
    turn: Turn

    @property
    def text(self) -> str:
        """送给用户的文本：回执 + 必要的追问（确认卡/验证码提示），去重拼接。"""
        parts = [self.turn.reply]
        if self.turn.ask and self.turn.ask not in self.turn.reply:
            parts.append(self.turn.ask)
        return "\n".join(part for part in parts if part)

    def as_dict(self) -> dict[str, Any]:
        """HTTP 响应体：字段全部取自 `Turn`（**不新增业务字段**，卡 18 的 `/api/chat` 可直接复用）。"""
        return {"trace_id": self.turn.trace_id, "intent": self.turn.intent,
                "tool_calls": list(self.turn.tool_calls), "tier": self.turn.tier,
                "executed": self.turn.executed, "pending_id": self.turn.pending_id,
                "reply": self.text}


def reply_to(text: str, *, peer_id: str) -> Reply:
    """IM 正文 → 编排层（**唯一入口**）：先按入口规则决定包裹/透传，再 `orchestrator.handle`。"""
    session_id = session_for(peer_id)
    payload = payload_for(text, session_id=session_id)
    return Reply(session_id=session_id, payload=payload,
                 turn=orchestrator.handle(payload, session_id=session_id))


@dataclass(frozen=True)
class Sent:
    """一次出消息的结果。`transport` 是实际用的通道（失败会降级成 loopback）。"""

    ok: bool
    transport: str
    detail: str = ""


class Sender(Protocol):
    """出消息接口（回环 / 飞书机器人两种实现）。"""

    name: str

    def send(self, text: str) -> Sent:                      # pragma: no cover - 协议声明
        ...


class LoopbackSender:
    """无网/未配置机器人时的出消息实现：什么都不发，由 `Channel` 记进 outbox。"""

    name = "loopback"

    def send(self, text: str) -> Sent:
        return Sent(ok=True, transport=self.name, detail="回环：未配置机器人 webhook")


class FeishuBotSender:
    """出消息：POST 飞书「自定义机器人」webhook（httpx **懒加载**，失败不抛异常、降级回环）。"""

    name = "feishu-bot"

    def __init__(self, webhook: str, secret: str = "", timeout: float = 5.0) -> None:
        self.webhook, self.secret, self.timeout = webhook, secret, timeout

    def send(self, text: str) -> Sent:
        try:
            import httpx                                     # 运行时可缺（dev 组依赖）
        except ImportError:
            return Sent(ok=False, transport="loopback", detail="未安装 httpx：出消息走回环")
        payload = feishu.outgoing_payload(text, timestamp=str(int(time.time())), secret=self.secret)
        try:
            response = httpx.post(self.webhook, json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return Sent(ok=False, transport="loopback", detail=f"出消息异常（{type(exc).__name__}）")
        ok = 200 <= response.status_code < 300
        detail = f"HTTP {response.status_code}"
        if not ok:
            detail = f"{detail}：{response.text[:120]}"
        return Sent(ok=ok, transport=self.name if ok else "loopback", detail=detail)


class Channel:
    """一条 IM 通道：去重 → 包裹 → 编排 → 出消息。

    线程安全（`threading.Lock` 保护 outbox 与去重表）：FastAPI 的**同步 handler 跑在线程池**里，
    而 `data/` 已按线程各持一份连接 + `BEGIN IMMEDIATE`（卡 16b），所以并发请求是安全的。
    """

    def __init__(self, config: ImConfig) -> None:
        self.config = config
        self.sender: Sender = (FeishuBotSender(config.bot_webhook, config.bot_secret,
                                              config.outbound_timeout)
                               if config.bot_webhook else LoopbackSender())
        self._lock = threading.Lock()
        self._outbox: list[dict[str, str]] = []
        self._seen: OrderedDict[str, None] = OrderedDict()

    def first_time(self, event_id: str) -> bool:
        """事件去重：第一次见到 → True；空 id 一律当新的（不误吞）。"""
        if not event_id:
            return True
        with self._lock:
            fresh = event_id not in self._seen
            self._seen[event_id] = None
            while len(self._seen) > SEEN_LIMIT:
                self._seen.popitem(last=False)
        return fresh

    def outbox(self) -> list[dict[str, str]]:
        """回环 outbox 快照（HTTP 轮询 demo 通道 + 自检用）。"""
        with self._lock:
            return [dict(item) for item in self._outbox]

    def deliver(self, text: str) -> Sent:
        """出消息：飞书机器人；失败或未配置 → 落进回环 outbox（无网也能把演示跑完）。"""
        sent = self.sender.send(text)
        with self._lock:
            self._outbox.append({"transport": sent.transport, "ok": str(sent.ok),
                                 "text": text, "detail": sent.detail})
            del self._outbox[:-OUTBOX_LIMIT]
        if not sent.ok:
            logger.warning("IM 出消息降级到回环：%s", sent.detail)
        return sent

    def handle(self, text: str, *, peer_id: str) -> Reply:
        """收到一条 IM 消息：包裹 → 编排层 → 出消息，返回 `Reply`。"""
        reply = reply_to(text, peer_id=peer_id)
        logger.info("IM 处理完成：peer=%s intent=%s trace=%s", peer_id, reply.turn.intent,
                    reply.turn.trace_id)
        self.deliver(reply.text)
        return reply
