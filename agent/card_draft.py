"""S5 可编辑卡片的数据契约：`CardDraft` 是**业务状态**，渲染在 `interfaces/web/`。

设计 §4：卡片草稿定义在 agent/（草稿是业务状态），渲染在 interfaces/web/ ——
界面只把编辑后的值交回 `write_flow.edit()`，**不直接改卡**（卡上数字必须来自 facts，
编辑 = 重走 shape → 解析/风控关卡 → 出新卡）。

敏感字段：可编辑集合里只有**名字/日期/金额**这类非号码字段（脱敏手机号是派生键、
本就不在可编辑集合里）；`sensitive` 标记留给将来把号码类字段加进来时用。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from agent import confirm_card

#: 各意图可编辑的字段（槽位键 → 卡面中文名）；**不在表里的意图不出编辑组件**
EDITABLE_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "transfer_single": (("payee", "收款人"), ("amount", "金额（元）")),
    "transfer_scheduled": (("payee", "收款人"), ("amount", "金额（元）"), ("schedule", "预约时间")),
    "gift_plan": (("contact", "收货人"), ("date", "日期"), ("budget", "预算（元）")),
}

#: 敏感字段（卡面一律脱敏展示）；当前可编辑集合里没有号码类字段，此表暂时为空、
#: 标记留着 —— 将来把手机号/卡号加进可编辑集合时，渲染层必须按它脱敏。
SENSITIVE: frozenset[str] = frozenset()

#: 改卡句式的**确定性解析**（不赌 LLM 脾气 —— 实测「金额改成 50 元」有时被判成 smalltalk）：
#: 只认「字段 改成/改为/变成 值」这种刻板句式；「改成 50」缺字段默认当改金额。
_EDIT_RE = re.compile(r"^(金额|收款人|收货人|预约时间|日期|预算|联系人)\s*(?:改成|改为|变成|调整为|设为)\s*(.+)$")
_BARE_EDIT_RE = re.compile(r"^(?:改成|改为|变成|调整为|设为)\s*(.+)$")
_LABEL_TO_SLOT = {"金额": "amount", "收款人": "payee", "收货人": "contact", "联系人": "contact",
                  "预约时间": "schedule", "日期": "date", "预算": "budget"}


def parse_edit(text: str, intent: str) -> dict | None:
    """把「金额改成 50 元」解析成 `{slot: 新值}`；不是改卡句式 / 字段不属于该意图 → None。

    规则在**编排层语义之内**（不猜意图 —— 意图来自在途确认卡）：句式刻板才认，
    值一律原样透传给 `write_flow.edit()`，由 shape/风控重新校验（不是直接改卡）。
    """
    if not text:
        return None
    editable = [slot for slot, _ in EDITABLE_FIELDS.get(intent, ())]
    if not editable:
        return None
    stripped = str(text).strip()
    match = _EDIT_RE.match(stripped)
    if match:
        slot = _LABEL_TO_SLOT.get(match.group(1))
        value = match.group(2).strip()
    else:
        match = _BARE_EDIT_RE.match(stripped)
        if match and "amount" in editable:               # 只说「改成 50」= 改金额
            slot, value = "amount", match.group(1).strip()
        else:
            return None
    if slot not in editable:
        return None
    if slot in ("amount", "budget"):
        value = re.sub(r"(元|块钱|块)$", "", value).strip()   # yuan_to_cents 只认纯数字写法
    return {slot: value}


class Field(BaseModel):
    name: str          # 槽位键（payee / amount / schedule / contact / date / budget）
    label: str         # 卡面中文名
    value: str = ""    # 当前值（来自确认卡的 filled 快照；敏感字段由渲染层脱敏）
    editable: bool = True
    sensitive: bool = False


class CardDraft(BaseModel):
    kind: str          # 意图（transfer_single / transfer_scheduled / gift_plan …）
    title: str
    status: str        # confirm / otp（与 confirm_card 的阶段同源）
    fields: list[Field] = []
    hint: str = "修改后重新走一遍解析与风控，不是直接改卡。"


def draft_of(session_id: str) -> CardDraft | None:
    """把在途确认卡翻译成可编辑草稿；没有在途卡（或该意图无编辑字段）→ None。"""
    confirmation = confirm_card.current(session_id)
    if confirmation is None:
        return None
    pairs = EDITABLE_FIELDS.get(confirmation.intent, ())
    if not pairs:
        return None
    fields = [Field(name=name, label=label,
                    value=str(confirmation.filled.get(name) or ""),
                    sensitive=name in SENSITIVE)
              for name, label in pairs]
    return CardDraft(kind=confirmation.intent,
                     title=f"{confirmation.intent} 确认卡",
                     status=str(confirmation.stage), fields=fields)
