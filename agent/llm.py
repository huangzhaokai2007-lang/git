"""编排层 LLM 客户端（卡 08）：OpenAI 兼容接口 + JSON 输出 + Pydantic 二次校验。

约束与来源：
- 卡 08 第 1 条：用 `openai` SDK 调 OpenAI 兼容接口，`base_url`/`key`/`model` 从 `.env` 读
  （变量名见 `.env.example`：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`）；提供 `chat_json(system, user, schema)`，
  用 `response_format=json_object` + Pydantic 二次校验；**超时 20s、失败重试 2 次**，仍失败抛 `LLMUnavailable`。
- 铁律 1：LLM 只负责「理解意图」与「措辞」——本模块只负责**取回结构化 JSON**，不判权限、不碰业务数字。
- 铁律 6：不引入新依赖（`openai>=1.40`、`python-dotenv>=1.0` 已在 `pyproject.toml`）；
  **import 阶段不联网**、不构造客户端 —— 没配 key 的环境也必须能起（评测要求一条命令跑起来）。
- 铁律 7：不可信文本（用户原话）只作为 **user 消息**传入，绝不拼进 system prompt（由调用方保证）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import TypeVar

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
TIMEOUT_SECONDS = 20.0     # 来源：卡 08 第 1 条「超时 20s」
MAX_RETRIES = 2            # 来源：卡 08 第 1 条「失败重试 2 次」→ 总尝试次数 = 1 + 2
TEMPERATURE = 0.0          # 口径：意图分类要可复现（规格未规定，记「需要人类决定」）
DEFAULT_BASE_URL = "https://api.deepseek.com"    # 来源：`.env.example` 里的默认值
DEFAULT_MODEL = "deepseek-chat"                  # 来源：`.env.example` 里的默认值

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """LLM 不可用：缺配置 / 超时 / 网络异常 / 连续多次拿不到合法 JSON。

    调用方（编排层）据此降级或转人工；本模块**不吞异常、不返回假数据**（铁律 2、5）。
    """


def load_settings() -> dict[str, str]:
    """读 `.env` + 进程环境（**已存在的环境变量优先**，便于单测 monkeypatch），返回三件套。"""
    load_dotenv(override=False)
    return {"base_url": os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
            "model": os.getenv("LLM_MODEL", DEFAULT_MODEL),
            "api_key": os.getenv("LLM_API_KEY", "")}


def build_client() -> OpenAI:
    """构造 OpenAI 兼容客户端（**每次调用新建**：单测可 monkeypatch 本函数，也不缓存失效连接）。

    缺 key → `LLMUnavailable`（在真正联网之前就失败，报错信息**不含** key 本身）。
    """
    settings = load_settings()
    if not settings["api_key"]:
        raise LLMUnavailable("未配置 LLM_API_KEY（变量名见 .env.example）")
    return OpenAI(base_url=settings["base_url"], api_key=settings["api_key"],
                  timeout=TIMEOUT_SECONDS)


def _complete(system: str, user: str) -> str:
    """单次调用，返回**原始 JSON 文本**；传输层异常统一归一化为 `LLMUnavailable`。"""
    settings = load_settings()
    try:
        response = build_client().chat.completions.create(
            model=settings["model"], temperature=TEMPERATURE, timeout=TIMEOUT_SECONDS,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    except OpenAIError as exc:            # 超时 / 连接失败 / HTTP 状态码异常都在这条基类之下
        raise LLMUnavailable(f"LLM 调用失败：{type(exc).__name__}") from exc
    choices = getattr(response, "choices", None) or []
    content = choices[0].message.content if choices else None
    if not content:
        raise LLMUnavailable("LLM 返回空内容")
    return content


def chat_json(system: str, user: str, schema: type[ModelT]) -> ModelT:
    """取回 JSON 文本并用 `schema` 做**二次校验**，返回模型实例。

    重试语义（卡 08 第 1 条）：总尝试 `1 + MAX_RETRIES` 次；「传输失败」与「JSON 非法 / 不符合 schema」
    都算失败。用尽仍失败 → 抛 `LLMUnavailable`。日志只记失败类别与尝试次数，**不记消息内容**。
    """
    attempts = MAX_RETRIES + 1
    last_reason = "未调用"
    for attempt in range(1, attempts + 1):
        try:
            raw = _complete(system, user)
            return schema.model_validate(json.loads(raw))
        except LLMUnavailable as exc:
            last_reason = f"调用失败（{exc}）"
        except json.JSONDecodeError:
            last_reason = "返回不是合法 JSON"
        except ValidationError:
            last_reason = "返回不符合 schema"
        logger.warning("chat_json 第 %s/%s 次失败：%s", attempt, attempts, last_reason)
    raise LLMUnavailable(f"连续 {attempts} 次未取到可用 JSON：{last_reason}")
