"""IM 通道配置（卡 17）：全部来自环境变量 / `.env`，**import 阶段不联网、不打印任何密钥**。

变量名与 `.env.example` 一一对应（铁律 6：不引新依赖 —— 只用已在 `pyproject.toml` 的
`python-dotenv` + `pydantic`）。缺省值即"无网可用"的最小配置：不配机器人 → 只能走本地回环。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

#: 默认监听地址：只绑本机（演示口径；要接飞书事件订阅得自己开公网反代，见 `interfaces/im/README.md`）
DEFAULT_HOST = "127.0.0.1"
#: 默认端口：避开 streamlit 的 8501 与 FastAPI 常见示例端口
DEFAULT_PORT = 8090
#: 出消息 HTTP 超时（秒）。规格未定义，取一个"演示不会被网络卡住"的值
OUTBOUND_TIMEOUT_SECONDS = 5.0


class ImConfig(BaseModel):
    """一条 IM 通道的进程级配置（纯数据，无逻辑）。"""

    model_config = ConfigDict(extra="forbid")

    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    #: 飞书「自定义机器人」出消息 webhook；**空 → 只走本地回环**（无网也能跑完整演示）
    bot_webhook: str = ""
    #: 机器人开启「签名校验」时的密钥；空 → 不签名
    bot_secret: str = ""
    #: 开放平台事件订阅的 Verification Token；空 → demo 不校验（安全说明见 README）
    verification_token: str = ""
    outbound_timeout: float = Field(default=OUTBOUND_TIMEOUT_SECONDS, gt=0.0)

    @property
    def outbound(self) -> str:
        """出消息方式：配了机器人 webhook → 飞书；否则回环（自检/无网演示用）。"""
        return "feishu-bot" if self.bot_webhook else "loopback"


def load_config() -> ImConfig:
    """读 `.env` + 进程环境（**已存在的环境变量优先**，便于演示时临时切换/单测 monkeypatch）。"""
    load_dotenv(override=False)
    return ImConfig(
        host=os.getenv("IM_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST,
        port=os.getenv("IM_PORT", str(DEFAULT_PORT)),
        bot_webhook=os.getenv("IM_BOT_WEBHOOK", "").strip(),
        bot_secret=os.getenv("IM_BOT_SECRET", "").strip(),
        verification_token=os.getenv("IM_VERIFICATION_TOKEN", "").strip(),
    )
