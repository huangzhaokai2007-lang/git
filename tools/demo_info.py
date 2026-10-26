"""演示预设（**T53 `demo_presets`**，L0 只读）—— 让演示者/评委在界面上就能看到"该用什么输入"。

为什么需要它：预置卡号与密码原本只写在 `data/card_binding_seed.py` 里（代码可见、界面不可见），
演示时要翻代码；种子库重置后 `agent_card_secret` 表甚至还不存在（首次绑卡才 provision）。
这个工具把这些**公开的合成夹具**集中暴露出来，供界面「演示预设」面板渲染。

只读、不写库、不触发任何 provision；**不含任何真实卡号/密码**（全部是 `data/card_binding_seed`
里标记为「明确公开的测试夹具」的合成值）。
"""

from __future__ import annotations

import datetime

from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _ok, current_user_id, require_owned,
)
from tools.schemas import ToolResult

PRESETS_TOOL = "demo_presets"


def demo_presets() -> ToolResult:
    """T53 返回演示用的合成预设（L0）。`data` = `{cards, payees, limits, otp, night}`。"""
    try:
        require_owned("演示预设", DEMO_USER_ID, current_user_id(),
                      tool=PRESETS_TOOL, intent="demo_presets")
    except ToolError as exc:
        return _fail(exc.code, exc.message)

    from data import dao
    from data.card_binding_seed import DEMO_CREDENTIALS
    from tools import _transfer_risk, payee, transfer

    cards = [{"card_no": number, "password": pin, "label": "演示卡"}
             for _card_id, number, pin in DEMO_CREDENTIALS]

    listing = payee.list_payees()
    profiles = listing.facts.get("profiles") or {}
    payees = []
    for item in listing.facts.get("items") or []:
        extra = profiles.get(str(item.get("payee_id"))) or {}
        kind = ("黑名单" if extra.get("blacklist")
                else "白名单" if item.get("is_whitelist") else "普通")
        payees.append({"name": item.get("name"), "phone": item.get("masked_phone"),
                       "kind": kind,
                       "quota_yuan": (extra.get("whitelist_quota") or 0) / 100 or None,
                       "expire": extra.get("whitelist_expire") or "长期"})

    limits = _transfer_risk._limits(_transfer_risk._today_out_flows(transfer._now()))
    facts = {"otp": "123456",
             "limits_single_max_yuan": f"{limits['single_max'] / 100:,.2f}",
             "limits_daily_max_yuan": f"{limits['daily_max'] / 100:,.2f}",
             "night_from_hour": _transfer_risk.NIGHT_START_HOUR,
             "night_to_hour": _transfer_risk.NIGHT_END_HOUR,
             "card_count": len(cards), "payee_count": len(payees)}
    return _ok({"cards": cards, "payees": payees, "facts": facts}, facts,
               f"演示预设：{len(cards)} 张演示卡 · {len(payees)} 位收款人")
