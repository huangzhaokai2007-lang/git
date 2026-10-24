"""审计回放的编排层入口（界面用）：把 T52 的原始行拼成**人类可读的链路**。

分层：只调 `tools/audit.replay_trace`（L0 只读），不碰 SQL、不写业务。
措辞在这里，**数字/工具名/档位全部照抄事实包**（铁律 2）。
"""

from __future__ import annotations


def replay(trace_id: str) -> tuple[list[str], list[str], dict]:
    """返回 `(链路行, 风险因子行, 原始 data)`；trace 不存在 → 空列表。"""
    from tools import audit

    result = audit.replay_trace(trace_id)
    if not result.ok:
        return [], [], {"error": result.message}
    data = dict(result.data or {})
    lines: list[str] = []
    for index, step in enumerate(data.get("steps") or [], start=1):
        head = f"{index}. [{step.get('result') or '—'}] {step.get('tool') or '（编排层）'}"
        tail = f"意图 {step.get('intent') or '—'} · 档位 {step.get('tier') or '—'}"
        if step.get("error_code"):
            tail += f" · 错误码 {step['error_code']}"
        params = step.get("params") or {}
        detail = "、".join(f"{key}={value}" for key, value in list(params.items())[:6])
        lines.append(" · ".join(x for x in (head, tail) if x) + (f"\n    {detail}" if detail else ""))
    factors = [f"{item.get('factor')} → {item.get('action_taken')}（{item.get('detail') or ''}）"
               for item in (data.get("factors") or [])]
    return lines, factors, data
