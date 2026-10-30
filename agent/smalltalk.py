"""闲聊也要往业务上靠（用户 2026-10-31）。

现状：「你好」被判成 `smalltalk` 后走通用兜底 → 「「闲聊」这个功能还没接通」，
既冷淡又没引导。改成：**一句热络的回应 + 顺手给业务入口**（不编任何数字，铁律 2）。

纯规则匹配（不调 LLM）：快、稳、可测；措辞全部是"能做什么"，不含金额/余额等事实数字。
"""

from __future__ import annotations

#: 关键词 → (开场回应, 业务引导)。顺序敏感：先匹配到的先生效。
_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("你好", "您好", "hi", "hello", "哈喽", "在吗", "在么", "早上好", "中午好", "下午好", "晚上好"),
     "您好呀，我在的 👋",
     "想办点什么？我可以帮你「查余额」「给谁转多少」「群收款」「看账单」。"),
    (("谢谢", "多谢", "感谢", "辛苦", "thx", "thanks"),
     "不客气，能帮上就好 🙌",
     "还想再办一笔吗？转账、收款人管理、群收款我都能接着做。"),
    (("再见", "拜拜", "走了", "晚安", "bye"),
     "好，随时叫我 👋",
     "下次直接说「给王五转 100」「查余额」这种话就行，我会先出确认卡给你核。"),
    (("你是谁", "你叫什么", "介绍一下", "你能做什么", "会什么", "能干嘛"),
     "我是这个演示环境里的银行智能助手 🏦",
     "能做的四类事：查（余额/账单/流水）、转（单笔/预约/循环）、收（群收款/收款人）、"
     "管（卡、订阅、理财）。"),
    (("天气", "吃饭", "累", "无聊", "周末", "放假", "加班", "闲聊", "心情"),
     "哈哈，这个我可聊不出干货 😄",
     "不过手上的事我能干：查余额、看账单、转账、群收款 —— 想先看哪个？"),
    (("测试", "在不在", "有人吗", "试试"),
     "在的在的，随时能试 👌",
     "最常用的是「查余额」和「给王五转 100」，说一句就行。"),
)

#: 都没匹配上时的兜底（仍然是"往业务靠"）
_FALLBACK = ("我在的 🙂 不过这类话题我聊不出花来。",
             "要不我们办点实事：查余额、看账单、转账、群收款 —— 你说一个方向？")


#: 聊不来的话题（分类给了 out_of_scope）：客气婉拒 + 把业务入口摆出来
_UNSUPPORTED = ("这个我确实帮不上忙，抱歉 🙏",
                "我能办的都是银行业务：查余额/账单、转账、预约转账、群收款、卡片管理、理财。"
                "要不要从「查余额」开始？")


def reply_for(text: str, *, intent: str = "smalltalk") -> str:
    """给一句回应：**一句热络/婉拒 + 一句业务引导**（不含任何业务数字，守铁律 2）。"""
    lowered = str(text or "").strip().lower()
    for keywords, greeting, nudge in _RULES:
        if any(word in lowered for word in keywords):
            return f"{greeting}\n\n{nudge}"
    if intent == "out_of_scope":
        greeting, nudge = _UNSUPPORTED
        return f"{greeting}\n\n{nudge}"
    greeting, nudge = _FALLBACK
    return f"{greeting}\n\n{nudge}"


def is_smalltalk(text: str) -> bool:
    """这句是不是我们覆盖到的闲聊（供用例与调试用）。"""
    lowered = str(text or "").strip().lower()
    return any(word in lowered
               for keywords, _, _ in _RULES for word in keywords)
