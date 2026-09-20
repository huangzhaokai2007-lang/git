"""评测入口 HTTP API（卡 18 + 卡 20 追加）：`POST /api/chat`、`POST /api/payee`。

契约（字段名冻结，**两个端点同形状**）：

    POST /api/chat    {"text": "查一下余额", "session_id": "可选（多轮确认/验证码用）"}
    POST /api/payee   {"name": "王小明", "phone": "13812345678", "session_id": "可选"}
    → {"reply": "...", "intent": "...", "tool_calls": ["..."],
       "tier": null, "executed": false, "trace_id": "trace-…"}          ← 恰好这 6 个字段

分层（CLAUDE.md 铁律，`tests/test_web_layering.py` 有 AST 守卫）：本层**只**调 `agent/orchestrator`
—— 与网页端（卡 16）、IM 通道（卡 17）是同一个入口，不复制任何业务逻辑；不判意图、不判权限、不算数字。
`/api/payee` 与 Streamlit 的表单**共用** `orchestrator.submit_payee`（卡 20 的渠道无关要求：
手机端/H5/小程序拿不到 Streamlit 进程内入口）。
**import 阶段无副作用**：不建库、不联网、不读密钥（没配 `LLM_API_KEY` 也能起，只是回答会降级/追问）。

口径（规格未定义处，记在交付说明）：`session_id` 是**可选**的加分项（确认卡/验证码这类多轮要用），
不影响"只要一个 `text` 也能跑"；请求文本按**第一方通道**处理（与网页端一致，不套 `untrusted_data`
外壳 —— 那是给 IM 这类第三方通道正文用的，见 `agent/channel.py`）。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from agent import orchestrator

#: 响应字段（**顺序即契约，不得增删**；两个端点共用同一份映射，见 `_turn_payload`）
RESPONSE_FIELDS = ("reply", "intent", "tool_calls", "tier", "executed", "trace_id")


class ChatIn(BaseModel):
    """一次评测请求：用户原话 + 可选会话标识。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str | None = None


class PayeeIn(BaseModel):
    """自助添加收款人（卡 20）：姓名 + 手机号；`session_id` 可选。

    只做结构性校验（两个字段必填）—— 手机号格式、脱敏、归属、落库全在 `tools/payee.py`；
    非法输入不会抛 5xx，而是走工具层回执（`executed=false` + 错误文案），与 `/api/chat` 一致。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    phone: str
    session_id: str | None = None


def _turn_payload(turn: Any) -> dict[str, Any]:
    """把 `Turn` 落成契约响应 —— **唯一**一份字段映射，两个端点共用（保证形状绝不漂移）。"""
    return {"reply": turn.reply, "intent": turn.intent, "tool_calls": list(turn.tool_calls),
            "tier": turn.tier, "executed": turn.executed, "trace_id": turn.trace_id}


def chat_payload(text: str, *, session_id: str | None = None) -> dict[str, Any]:
    """走编排层并把 `Turn` 落成契约响应。"""
    return _turn_payload(orchestrator.handle(text, session_id=session_id))


def payee_payload(name: str, phone: str, *, session_id: str | None = None) -> dict[str, Any]:
    """走 `orchestrator.submit_payee`（与 Streamlit 表单**同一个 agent 入口**）并落成契约响应。"""
    turn = orchestrator.submit_payee(name, phone, session_id=session_id or f"api-{uuid.uuid4().hex[:8]}")
    return _turn_payload(turn)


def create_app() -> FastAPI:
    """构造 FastAPI 应用（`/api/chat` + `/api/payee` + `/healthz`）。"""
    app = FastAPI(title="AI Banking Agent · 评测入口（模拟环境 / 合成数据）", version="0.1.0")

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        return chat_payload(body.text, session_id=body.session_id)

    @app.post("/api/payee")
    def payee(body: PayeeIn) -> dict[str, Any]:
        """自助添加收款人（§5：`payee_add` → L1，不发确认卡、不要 OTP；表单提交本身即用户确认）。"""
        return payee_payload(body.name, body.phone, session_id=body.session_id)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """存活探针（不回显任何配置/密钥）。"""
        return {"status": "ok", "endpoints": ["/api/chat", "/api/payee"], "version": app.version}

    return app
