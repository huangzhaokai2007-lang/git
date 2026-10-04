"""模板模块（简化版）。"""

from __future__ import annotations

import re


def refuse(reason: str) -> str:
    return f"抱歉，您的请求涉及安全问题，没法执行。原因：{reason}"


def unsupported(intent: str) -> str:
    return f"「{intent}」功能还没接通，敬请期待。"


def clarify_low_confidence() -> str:
    return "抱歉，我没有理解您的意思，能否请您再详细说明一下？"


def clarify_missing(missing: list[str]) -> str:
    labels = {"date_from": "起始日期", "date_to": "结束日期", "period": "账期"}
    names = [labels.get(m, m) for m in missing]
    return f"请提供以下信息：{'、'.join(names)}"


def clarify_to_human() -> str:
    return "抱歉，我无法理解您的需求，将为您转接人工服务。"


def tool_error(code: str | None, message: str) -> str:
    return f"查询失败：{message}"


def compose_reply(intent: str, slots: dict, result: object) -> tuple[str, bool, int]:
    """组合回执。"""
    return result.message, False, 1


def verify_numbers(reply: str, facts: dict) -> set[str]:
    """验证回执中的数字是否都在 facts 中。"""
    numbers = set(re.findall(r'\d[\d,]*\.\d{2}', reply))
    fact_numbers = set()
    for v in facts.values():
        if isinstance(v, str):
            fact_numbers.update(re.findall(r'\d[\d,]*\.\d{2}', v))
    return numbers - fact_numbers


def render(template_name: str, facts: dict) -> str:
    """渲染模板。"""
    raise TemplateError("模板未实现")


class TemplateError(Exception):
    pass


def polish(text: str, facts: dict) -> str | None:
    """润色文本。"""
    return text
