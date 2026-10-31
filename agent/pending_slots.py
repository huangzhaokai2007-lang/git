"""缺槽追问后的**续接**：用户下一句短答，按 `missing` 顺序补回原意图，而不是当成新请求。

用户实测（2026-10-27）：
```
我：我要送礼
系统：请补充：联系人、日期、预算、平台、收货地址、平台订单号。
我：京东，家，JD20261024001        ← 这是**补槽**，却被当成新意图
系统：连续两次没能确认您的需求，我先转人工客服帮您处理。  ❌
```

设计：
- `write_flow.start` 产生「缺槽追问」时登记 `(intent, slots, missing)`；
- 下一句进来先尝试 `merge()`：按 `missing` 顺序把短答填进去（支持 `、，,` 与空格切分）；
- 填得动 → 用合并后的槽位重跑 `write_flow.start`（等价于用户把话一次说完）；
- 填不动（像新请求 / 太长 / 想取消）→ 清掉登记，走正常分类。

只影响「追问之后的那一句」，不动任何业务判定。
"""

from __future__ import annotations

import re
import time
from typing import Any

#: 登记多久有效（秒）—— 超过就当作新对话
TTL_SECONDS = 600

#: 看起来是「新请求」的动词/句式：出现就不当补槽（交给正常分类）
_NEW_REQUEST = re.compile(r"(转|送|查|买|退|转账|汇款|收款|送礼|礼物|预约|定时|查询|查一下|我的|账单|流水|余额|"
                          r"取消|撤销|删除|确认|验证码|加入|移出|拉黑|解除|设置|改成)")
#: 补槽答案的切分：顿号/逗号/分号/空格
_SPLIT = re.compile(r"[、,，;；\s]+")

_PENDING: dict[str, dict[str, Any]] = {}


def remember(session_id: str, intent: str, slots: dict, missing: list[str]) -> None:
    """登记「还差哪些槽位」（`write_flow.start` 出追问时调用）。"""
    if not session_id or not intent or not missing:
        return
    _PENDING[session_id] = {"intent": intent, "slots": dict(slots),
                            "missing": [slot for slot in missing if slot], "at": time.monotonic()}


def remember_prefill(session_id: str, intent: str, slots: dict) -> None:
    """登记**只用于预填**的槽位（不缺东西，例如群收款要先铺一张可编辑卡）。

    与 `remember()` 的区别：`missing` 允许为空；`slot_form_ui` 看到 missing 为空不会出填表卡。
    """
    if not session_id or not intent:
        return
    _PENDING[session_id] = {"intent": intent, "slots": dict(slots),
                            "missing": [], "prefill": True, "at": time.monotonic()}


def clear(session_id: str | None = None) -> None:
    """清掉登记（会话重置 / 已补齐 / 用户改口时用）。"""
    if session_id is None:
        _PENDING.clear()
    else:
        _PENDING.pop(session_id, None)


def peek(session_id: str) -> dict | None:
    """看一眼有没有未完成的补槽（过期自动清）。"""
    item = _PENDING.get(session_id)
    if item is None:
        return None
    if time.monotonic() - float(item["at"]) > TTL_SECONDS:
        _PENDING.pop(session_id, None)
        return None
    return item


def merge(item: dict, text: str) -> dict | None:
    """把一句话按 `missing` 顺序填进 `item["slots"]`；填不动 → `None`。

    规则（确定性，不猜）：
    - 出现「新请求」动词 → `None`（让正常分类处理）；
    - 按 `、,，;；空格` 切分，**依次**填进 missing 的槽位；
    - 分片比槽位多 → 余下部分并入**最后一个**槽位（"京东 家 上海市某某路1号" 这种）；
    - 一片都没填上 → `None`。
    """
    raw = str(text or "").strip()
    if not raw or _NEW_REQUEST.search(raw) or raw in ('算了', '算了吧', '不办了', '先不办了', '谢谢', '好的', '你好', '重试', '再试一下'):
        return None
    parts = [part for part in _SPLIT.split(raw) if part]
    missing = list(item.get("missing") or [])
    if not parts or not missing:
        return None
    filled = dict(item["slots"])
    for index, slot in enumerate(missing):
        if index >= len(parts):
            break
        if index == len(missing) - 1 and len(parts) > len(missing):
            filled[slot] = "".join(parts[index:])      # 余下全给最后一个槽位（地址常带空格）
        else:
            filled[slot] = parts[index]
    if filled == item["slots"]:
        return None
    return filled


def remaining(item: dict, filled: dict) -> list[str]:
    """合并后还缺哪些槽位（用于决定是继续等补槽还是收工）。"""
    return [slot for slot in item["missing"] if not str(filled.get(slot) or "").strip()]


def submit_form(session_id: str, values: dict):
    """Continue the owned pending request through normal orchestration and audit."""
    from agent import classifier, orchestrator
    item = peek(session_id)
    if item is None:
        raise ValueError('这张填表卡已失效，请重新发起请求。')
    slots = {**item['slots'], **{key: value for key, value in values.items()
                               if key in item['missing'] and value}}
    return orchestrator.handle('（表单填写）', session_id=session_id,
        intent_resolver=lambda *_: classifier.IntentOut(intent=item['intent'], confidence=1, slots=slots))


def read_answer(session_id: str, verdict: Any) -> Any | None:
    """Read clarifications stay reads, rather than entering the write state machine."""
    item = peek(session_id)
    if not item or item['intent'] not in ('anomaly_check', 'bill_report', 'bill_analysis', 'txn_query'):
        return None
    if verdict.intent != item['intent']:
        return None
    from agent.classifier import IntentOut
    clear(session_id)
    return IntentOut(intent=verdict.intent, confidence=verdict.confidence,
                     slots={**item['slots'], **verdict.slots})


def resume(session_id: str, text: str, verdict: Any, *, trace_id: str = "") -> Any | None:
    """尝试续接（**在分类之后**调用，用分类结果决定怎么合并）。

    三种情况：
    1. 分类给出了**同一个意图**且有槽位 → 用户把话说全了：**分类器槽位优先**，与旧槽位合并；
    2. 分类给的是 `smalltalk` / `out_of_scope`（不像请求）→ 当成**按序补槽**（`merge`）；
    3. 其它意图（用户改口/换了件事）→ 清掉登记，返回 `None`，走正常流程。
    """
    item = peek(session_id)
    if item is None:
        return None
    intent = str(getattr(verdict, "intent", "") or "")
    slots = dict(getattr(verdict, "slots", None) or {})
    if intent == item["intent"] and slots:
        merged = {**item["slots"], **slots}
    elif intent in ("smalltalk", "out_of_scope"):
        merged = merge(item, text)
        if merged is None:
            return None
    else:
        clear(session_id)
        return None

    from agent import write_flow

    if item['intent'] not in write_flow.WRITE_INTENTS:
        return None
    step = write_flow.start(item["intent"], merged, session_id, trace_id=trace_id)
    if step.missing:
        _PENDING[session_id] = {**item, "slots": merged, "missing": list(step.missing),
                                "at": time.monotonic()}
    else:
        _PENDING.pop(session_id, None)
    return step
