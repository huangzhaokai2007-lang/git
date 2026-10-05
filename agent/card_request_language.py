"""Web card language shortcuts: full requests only; other actions use the classifier."""
from __future__ import annotations

import re

CARD = r"(?:银行卡|储蓄卡|信用卡|卡片|卡)"
PREFIX = (
    r"(?:你好[，,]?|您好[，,]?|请问|请|麻烦你|麻烦您|麻烦|劳烦|帮我|给我|替我|"
    r"我想要|我想|我要|我需要|我打算|我希望|我准备|能不能|能否|可以|能|帮忙){0,4}"
)
SUFFIX = r"(?:吗|呢|吧|呀|啊|可以吗|好吗)?(?:[，,]?(?:谢谢你|谢谢您|谢谢|麻烦了))?[？?！!。，,]*"
QUALIFIER = r"(?:我的|我|这张|这|一张|张|新的|新){0,3}"
RULES = {
    "query": (
        rf"(?:查询|查查|查|查看|看看|看|显示|列出|列一下|展示)(?:一下|下)?"
        rf"(?:我的|我名下的?|我已绑定的?|我绑定的?|已绑定的?)?(?:所有|全部)?(?:的)?{CARD}(?:列表|信息|详情|状态)?",
        rf"(?:看看|查看|查(?:询)?(?:一下)?)?我(?:名下)?(?:有|已(?:经)?绑定了?|绑定了)(?:哪些|几张|多少张){CARD}",
        rf"我(?:的|名下的?)?{CARD}(?:在哪里|列表)?",
        rf"{CARD}(?:查询|列表|信息)",
    ),
    "bind": (
        rf"(?:绑定|绑|添加|新增|关联)(?:一下|下)?{QUALIFIER}{CARD}(?:一下|下)?",
        rf"{CARD}(?:绑定|添加|关联)",
        rf"我(?:有|已有)(?:一)?张{CARD}[，,]?(?:想|要|需要)(?:绑定|绑|添加|关联)(?:一下)?",
        rf"把{QUALIFIER}{CARD}(?:绑定|绑|添加|关联)(?:一下|上|进来|到这里)?",
        rf"(?:怎么|如何|怎样)(?:绑定|绑|添加|关联){CARD}",
    ),
    "apply": (
        r"(?:申请|办理|办|申请办理)(?:一下|下)?(?:一张|张|新的|新){0,2}信用卡",
        r"信用卡(?:怎么|如何|怎样)(?:申请|办理|办)",
        r"(?:怎么|如何|怎样)(?:申请|办理|办)(?:一张)?信用卡",
    ),
}
PATTERNS = tuple((kind, re.compile(PREFIX + body + SUFFIX))
                 for kind, rules in RULES.items() for body in rules)


def recognize(text: str) -> str | None:
    """Normalize spacing and courtesy words, never extract an intent from a mixed sentence."""
    request = re.sub(r"\n（当前日期：[0-9]{4}-[0-9]{2}-[0-9]{2}）$", "", text)
    request = re.sub(r"\s+", "", request)
    if len(request) > 120 or re.search(r"不要|不想|不需要|无需|别|取消|解绑|解除绑定", request):
        return None
    return next((kind for kind, pattern in PATTERNS if pattern.fullmatch(request)), None)
