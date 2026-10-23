"""S5 可编辑卡片的数据契约：`CardDraft` 是**业务状态**，渲染在 `interfaces/web/`。

设计 §4：卡片草稿定义在 agent/（草稿是业务状态），渲染在 interfaces/web/ ——
界面只把编辑后的值交回 `write_flow.edit()`，**不直接改卡**（卡上数字必须来自 facts，
编辑 = 重走 shape → 解析/风控关卡 → 出新卡）。

敏感字段：可编辑集合里只有**名字/日期/金额**这类非号码字段（脱敏手机号是派生键、
本就不在可编辑集合里）；`sensitive` 标记留给将来把号码类字段加进来时用。
"""

from __future__ import annotations

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
