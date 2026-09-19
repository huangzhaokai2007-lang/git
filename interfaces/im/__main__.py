"""`python -m interfaces.im`：起 IM 服务 / 跑离线自检 / 打印配置指引。

用法（都在仓库根目录跑）：

    uv run python -m interfaces.im --selftest     # 无网自检（不占端口、不需要 LLM key）
    uv run python -m interfaces.im --guide        # 只看配置指引，不起服务
    uv run python -m interfaces.im                # 起服务（默认 127.0.0.1:8090）

⚠ 本模块只做命令行调度，不写业务逻辑。
"""

from __future__ import annotations

import logging
import sys

from interfaces.im.config import ImConfig, load_config

logger = logging.getLogger(__name__)


def _print_guide(config: ImConfig) -> None:
    """打印配置指引（**不回显任何 webhook / 密钥**，只报"配没配"）。"""
    print("IM 通道配置（变量名见 .env.example）")
    print(f"  出消息方式 : {config.outbound}"
          + ("（已配置 IM_BOT_WEBHOOK）" if config.bot_webhook else "（未配置机器人 → 只走本地回环）"))
    print(f"  监听        : http://{config.host}:{config.port}")
    print(f"  事件订阅入口: POST /im/webhook"
          + (f"（已配置 IM_VERIFICATION_TOKEN，会校验 token）" if config.verification_token
             else "（未配置 IM_VERIFICATION_TOKEN：demo 不校验 token）"))
    print("  本地回环演示: POST /im/loopback  {\"text\": \"查一下余额\"}   /   GET /im/outbox 轮询回执")
    print("  造数据      : uv run python -m data.seed --reset")
    print("  接飞书      : 见 interfaces/im/README.md（需要公网可达地址）")


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`--selftest` / `--guide` / 默认起服务。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        from interfaces.im.selftest import main as selftest      # 懒加载：起服务时不必 import
        return selftest()
    config = load_config()
    _print_guide(config)
    if "--guide" in args:
        return 0
    import uvicorn                                               # 已在 pyproject（铁律 6）
    from interfaces.im.server import create_app
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(config), host=config.host, port=config.port)
    return 0


if __name__ == "__main__":                                       # pragma: no cover - CLI 入口
    raise SystemExit(main())
