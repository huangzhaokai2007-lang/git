"""收款人流（占位）。"""

from __future__ import annotations

INTENT = "payee_add"
PAYEE_ADD_TIER = "L1"
PROMPT = "请填写收款人信息"


def submit_payee(name: str, phone: str) -> dict:
    return {"payee_id": "payee_new", "name": name, "masked_phone": phone[:3] + "****" + phone[-4:]}
