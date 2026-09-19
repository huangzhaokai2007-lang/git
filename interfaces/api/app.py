"""评测入口 HTTP API（卡 18）：`POST /api/chat` —— 一行请求走**同一个编排层**。

契约（剧本卡 18 第 2 条，字段名冻结）：

    POST /api/chat   {"text": "查一下余额", "session_id": "可选（多轮确认/验证码用）"}
    → {"reply": "...", "intent": "balance_query", "tool_calls": ["get_balance"],
       "tier": null, "executed": false, "trace_id": "trace-…"}

分层（CLAUDE.md 铁律，`tests/test_web_layering.py` 有 AST 守卫）：本层**只**调 `agent/orchestrator`
—— 与网页端（卡 16）、IM 通道（卡 17）是同一个入口，不复制任何业务逻辑；不判意图、不判权限、不算数字。
**import 阶段无副作用**：不建库、不联网、不读密钥（没配 `LLM_API_KEY` 也能起，只是回答会降级/追问）。

口径（规格未定义处，记在交付说明）：`session_id` 是**可选**的加分项（确认卡/验证码这类多轮要用），
不影响"只要一个 `text` 也能跑"；请求文本按**第一方通道**处理（与网页端一致，不套 `untrusted_data`
外壳 —— 那是给 IM 这类第三方通道正文用的，见 `agent/channel.py`）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from agent import orchestrator

#: 响应字段（**顺序即契约，不得增删**）
RESPONSE_FIELDS = ("reply", "intent", "tool_calls", "tier", "executed", "trace_id")


class ChatIn(BaseModel):
    """一次评测请求：用户原话 + 可选会话标识。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str | None = None


def chat_payload(text: str, *, session_id: str | None = None) -> dict[str, Any]:
    """走编排层并把 `Turn` 落成契约响应（本模块**唯一**的业务入口）。"""
    turn = orchestrator.handle(text, session_id=session_id)
    return {"reply": turn.reply, "intent": turn.intent, "tool_calls": list(turn.tool_calls),
            "tier": turn.tier, "executed": turn.executed, "trace_id": turn.trace_id}


def create_app() -> FastAPI:
    """构造 FastAPI 应用（`/api/chat` + `/healthz`）。"""
    app = FastAPI(title="AI Banking Agent · 评测入口（模拟环境 / 合成数据）", version="0.1.0")

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        return chat_payload(body.text, session_id=body.session_id)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """存活探针（不回显任何配置/密钥）。"""
        return {"status": "ok", "endpoint": "/api/chat", "version": app.version}

    return app
