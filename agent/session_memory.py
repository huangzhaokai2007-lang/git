"""会话内的最近几轮对话 —— **追问续接**要的上下文（F17 修复下沉到编排层）。

**为什么放在编排层而不是各入口**（系统性自检的结论）：追问续接需要「上一轮问了什么」。
此前只有网页端手工拼 `history`，而 `interfaces/api`、`interfaces/im`、`app/cli` **三个入口都没传**
（`orchestrator.handle(payload, session_id=…)`）—— 于是同一句「列出两个李四 → 用户回 1001」
在 API / IM / CLI 上一律判成 `smalltalk`，收款人永远解析不出来。把记忆挂在 `orchestrator.handle`，
四个入口自然一致，也不会再出现「新加一个入口忘了传 history」。

**为什么只留 4 条**：分类器把每条历史拼成一条 user 消息 + 边界标记，留多了只会让提示词无限增长。
4 条正好覆盖「追问 + 回答」的来回（用户话与助手回执各两轮）。

**进程内、无持久化**：会话记忆丢失的唯一后果是**追问接不上**（用户再完整说一遍即可），
不影响资金、账务或审计 —— 与 `confirm_card` 的在途态同寿命，进程重启即清空。
"""

from __future__ import annotations

#: 每个会话保留多少条历史（用户话与助手回执交替计数）
CAP = 4

#: 会话 → 最近几条对话文本（进程内）
_RECENT: dict[str, list[str]] = {}


def recent(session_id: str, explicit: list[str] | None = None) -> list[str]:
    """本次分类该用的 history：**显式传入优先**，否则用会话里记住的最近几条。

    显式优先是为了让「调用方比编排层更懂上下文」的场合（如网页端自己的会话记录、测试脚本）
    仍可覆盖；传 `None` 才回落到记忆。
    """
    return list(explicit) if explicit is not None else list(_RECENT.get(session_id, ()))


def remember(session_id: str, user_text: str, reply: str) -> None:
    """记住这一轮（用户原话 + 回执）：下一轮追问时它们就是分类器的上下文。

    **必须记原始 `text`**（不是界面上脱敏后的 `display`）—— 分类器要看的是用户真正说了什么。
    """
    _RECENT[session_id] = (_RECENT.get(session_id, []) + [str(user_text), str(reply)])[-CAP:]


def reset(session_id: str | None = None) -> None:
    """清空会话记忆（测试隔离与界面「重置会话态」用）。传 `None` 清全部。"""
    from agent import scene_context, pending_slots
    scene_context.reset(session_id)
    pending_slots.clear(session_id)
    if session_id is None:
        _RECENT.clear()
    else:
        _RECENT.pop(session_id, None)
