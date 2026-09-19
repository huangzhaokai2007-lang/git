"""`python -m interfaces.api`：起评测入口服务（默认 `127.0.0.1:8000`）。

    uv run python -m data.seed --reset          # 首次：造合成数据
    uv run python -m interfaces.api             # 起服务
    curl -s -X POST http://127.0.0.1:8000/api/chat \\
      -H 'content-type: application/json' -d '{"text": "查一下余额"}'

容器里用 `API_HOST=0.0.0.0`（见 Dockerfile / docker-compose.yml）。
"""

from __future__ import annotations

import logging
import os

#: 默认只绑本机（本机演示）；容器/评测机用环境变量放开
DEFAULT_HOST = "127.0.0.1"
#: 默认端口（避开 streamlit 8501 与 IM 通道 8090）
DEFAULT_PORT = 8000


def main() -> int:
    """起 uvicorn 服务（阻塞直到进程被停）。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.getenv("API_HOST", DEFAULT_HOST)
    raw_port = os.getenv("API_PORT", str(DEFAULT_PORT))
    port = int(raw_port) if raw_port.isdigit() else DEFAULT_PORT
    if not raw_port.isdigit():
        logging.warning("API_PORT=%r 不是数字，回退到 %s", raw_port, DEFAULT_PORT)
    import uvicorn                                                # 已在 pyproject（铁律 6）

    from interfaces.api.app import create_app
    print(f"评测入口：POST http://{host}:{port}/api/chat    健康检查：GET /healthz")
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":                                        # pragma: no cover - CLI 入口
    raise SystemExit(main())
