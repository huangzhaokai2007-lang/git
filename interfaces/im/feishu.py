"""飞书协议适配（卡 17）：**纯函数**，不联网、不 import `tools`/`data`/`guard`，离线可单测。

两条路各覆盖一层：

- **入消息**（开放平台「事件订阅」回调）：
  - 保存回调地址时飞书先发 `{"type": "url_verification", "challenge": "..."}`，必须**原样回显** challenge；
  - 真实消息事件 `im.message.receive_v1`：正文在 `event.message.content`，它是一个 **JSON 字符串**
    （`{"text": "@_user_1 查一下余额"}`），`@_user_N` 提及要剥掉；
  - 事件**加密**模式要 AES 解密 → 会引新依赖（铁律 6 禁止）→ 本模块明确返回 `encrypted` 让上层
    回一句可执行的提示，而不是猜。
- **出消息**（「自定义机器人」webhook）：报文 `{"msg_type": "text", "content": {"text": ...}}`；
  机器人开了「签名校验」时附 `timestamp` + `sign`（官方口径：key = `f"{timestamp}\n{secret}"`，
  对**空串**做 HMAC-SHA256 再 base64）。

⚠ 本模块只**解析/构造报文**，不做任何业务判断，也不决定"要不要回复谁"（那是 `channel.py` 的事）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: 保存回调地址时的校验事件类型
CHALLENGE_TYPE = "url_verification"
#: 接收消息的事件类型（飞书 v2 schema）
MESSAGE_EVENT = "im.message.receive_v1"
#: 只处理文本消息（demo 口径）
TEXT_MESSAGE = "text"
#: 事件加密模式的提示（不引 AES 依赖）
ENCRYPTED_HINT = ("事件订阅开了加密：本 demo 不引 AES 解密依赖（铁律 6），"
                  "请在飞书后台把「加密」关掉或改用明文事件")
#: `@_user_1` 这类提及
_MENTION = re.compile(r"@_user_\d+")
#: 出消息 payload 的文本上限（飞书单条文本上限远大于此；这里只防超长刷屏）
MAX_TEXT_CHARS = 4000


@dataclass(frozen=True)
class Event:
    """一次回调的解析结果。

    `kind`：`challenge`（回显）/ `message`（有正文）/ `encrypted`（加密模式）/ `bad_token`（token 不符）
    / `ignore`（其它事件类型或结构不符）。只有 `kind == "message"` 才带正文。
    """

    kind: str
    challenge: str = ""
    text: str = ""
    peer_id: str = ""
    message_id: str = ""
    event_id: str = ""
    note: str = ""


def message_text(content: object) -> str:
    """`event.message.content` → 纯文本：解 JSON 字符串、剥 `@_user_N` 提及、去首尾空白。"""
    raw: object = content
    if isinstance(content, str):
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            return ""
    if isinstance(raw, Mapping):
        raw = raw.get("text", "")
    return _MENTION.sub("", str(raw)).strip()


def parse_event(payload: object, *, verification_token: str = "") -> Event:
    """解析事件订阅回调（**防御式**：结构不符一律 `ignore`，绝不对回调体做假设）。

    `verification_token` 非空时校验 `header.token`：不符 → `bad_token`（上层拒处理）。
    """
    if not isinstance(payload, Mapping):
        return Event(kind="ignore", note="回调体不是 JSON 对象")
    if payload.get("encrypt"):
        return Event(kind="encrypted", note=ENCRYPTED_HINT)
    if payload.get("type") == CHALLENGE_TYPE:
        return Event(kind="challenge", challenge=str(payload.get("challenge") or ""))
    header = payload.get("header")
    header = header if isinstance(header, Mapping) else {}
    event_type = str(header.get("event_type") or "")
    if event_type != MESSAGE_EVENT:
        return Event(kind="ignore", note=f"未处理的事件类型：{event_type or '（空）'}")
    if verification_token and str(header.get("token") or "") != verification_token:
        return Event(kind="bad_token", note="事件订阅 token 不匹配：拒绝处理")
    event = payload.get("event")
    event = event if isinstance(event, Mapping) else {}
    message = event.get("message")
    message = message if isinstance(message, Mapping) else {}
    message_id = str(message.get("message_id") or "")
    return Event(kind="message", text=message_text(message.get("content")),
                 peer_id=_peer_of(event, message), message_id=message_id,
                 event_id=str(header.get("event_id") or message_id))


def _peer_of(event: Mapping, message: Mapping) -> str:
    """会话标识：优先 `chat_id`（群/单聊），退回发送者 open_id，再退回 `unknown`。"""
    sender = event.get("sender")
    sender = sender if isinstance(sender, Mapping) else {}
    sender_id = sender.get("sender_id")
    sender_id = sender_id if isinstance(sender_id, Mapping) else {}
    return str(message.get("chat_id") or sender_id.get("open_id") or "unknown")


def challenge_echo(payload: object) -> dict:
    """URL 校验的响应体（飞书要求结构与 challenge 完全一致）。"""
    challenge = ""
    if isinstance(payload, Mapping):
        challenge = str(payload.get("challenge") or "")
    return {"challenge": challenge}


def sign(timestamp: str, secret: str) -> str:
    """自定义机器人签名（官方口径）：key = `f"{timestamp}\\n{secret}"`，对空串做 HMAC-SHA256 再 base64。"""
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode()


def outgoing_payload(text: str, *, timestamp: str = "", secret: str = "") -> dict:
    """出消息报文：文本消息；`secret` 非空则附 `timestamp` + `sign`。"""
    payload: dict = {"msg_type": "text", "content": {"text": str(text)[:MAX_TEXT_CHARS]}}
    if secret:
        payload["timestamp"] = str(timestamp)
        payload["sign"] = sign(str(timestamp), secret)
    return payload
