"""「现在该显示哪一张业务卡」的唯一裁决（用户 2026-10-30：卡片该消失不消失/乱出现/顺序乱）。

问题根源：每个 `*_ui` 各自用一堆 session 标记决定要不要画（`bound-*-query`、`card-edit-open`、
`aa-create-open`、`sched-edit`、解析卡 pending…）。这些标记**互相独立**，于是：
- 上一个话题的卡还挂着（该消失没消失）；
- 解析卡 5 分钟内一直有效，哪怕你已经换了件事（"胡乱出现收款人解析"）；
- 多个面板同时满足条件 → 谁先画谁在上面（"前后顺序有问题"）。

本模块给出**一个**答案：当前只允许哪一张业务卡在屏上。规则（优先级从上到下）：

1. 有**解析卡待选** → 只画解析卡（它在等用户决定，别的都让位）；
2. 有**在途确认卡** → 不画业务卡（确认卡本身自带「修改」抽屉）；
3. 否则按**最近一轮的意图**挑一张：收款人 / 预约 / 群收款 / 卡片；
4. 都不匹配 → 什么都不画。

纯函数，便于单测；`workspace_ui.panels` 只按它的结果渲染。
"""

from __future__ import annotations

#: 意图 → 业务面板（写意图也映射到它的"归属面板"，这样按钮操作后场景不乱跳）
INTENT_PANEL = {
    "payee_list": "payee", "payee_whitelist": "payee", "payee_whitelist_edit": "payee",
    "payee_whitelist_remove": "payee", "payee_blacklist": "payee",
    "payee_unblacklist": "payee", "payee_delete": "payee", "payee_note": "payee",
    "payee_tags": "payee", "payee_card_no": "payee", "payee_add": "payee",
    "scheduled_list": "schedule", "transfer_scheduled": "schedule",
    "scheduled_modify": "schedule", "scheduled_cancel": "schedule",
    "scheduled_run": "schedule",
    "aa_list": "aa", "aa_collect": "aa", "aa_mark_paid": "aa", "aa_cancel": "aa",
    "aa_remind": "aa",
    "gift_order_list": "gift",
    "card_query": "cards",
}

#: 面板名 → `bound-*-query` 标记（沿用既有状态，界面不必改）
PANEL_FLAGS = {"payee": "bound-payee-query", "schedule": "bound-schedule-query",
               "aa": "bound-aa-query", "gift": "bound-gift-query",
               "cards": "bound-card-query"}


#: 这些场景的确认卡**由业务卡自己画**，所以即使有在途确认也要继续渲染那张卡
_CONFIRM_INSIDE = {"aa": {"aa_collect"}}


def active_panel(*, intent: str | None, has_confirmation: bool, picker_pending: bool,
                 flags: dict | None = None) -> str | None:
    """当前唯一应该渲染的业务面板名（`payee` / `schedule` / `aa` / `gift` / `cards` / None）。

    `flags` 只在**没有明确意图**时兜底用（例如刷新页面后 turn 记录还在，但意图已消费）。
    """
    flags = flags or {}
    if picker_pending:                       # 解析卡在等用户点选：其它业务卡一律让位
        return None
    if has_confirmation:
        #: 两种「确认卡内嵌在业务卡里」的场景要继续画那张卡，否则确认按钮会跟着卡一起消失：
        #: ① 群收款（三卡合一：发起/确认/状态都在同一张卡）；② 收款人卡内联转账。
        if intent in _CONFIRM_INSIDE["aa"]:
            return "aa"
        if flags.get("payee-inline-intent"):
            return "payee"
        return None                          # 其它意图：确认卡自带修改抽屉，业务卡让位
    panel = INTENT_PANEL.get(str(intent or ""))
    if panel:
        return panel
    for name, flag in PANEL_FLAGS.items():   # 兜底：会话里还挂着的标记
        if flags and flags.get(flag):
            return name
    return None


#: 每个面板自己的「展开/编辑」子状态（面板切走/切回时一律复位，避免残留）
PANEL_STATE = {
    "payee": ("payee-open",),
    "schedule": ("sched-edit",),
    "aa": ("aa-create-open", "aa-pick-open", "aa-split-open"),
    "gift": (),
    "cards": (),
}


def clears_state(previous: str | None, current: str | None = None) -> dict:
    """换面板时要复位哪些子状态：**离场**与**入场**那张卡的展开态都清掉。

    2026-10-30（用户：卡片该消失不消失）：原来只管 `payee-open` / `sched-edit`，
    没管群收款的 `aa-create-open` —— 在群收款里点开「新增群收款」再去看收款人，
    回来表单还是展开的。
    """
    reset: dict = {}
    for name in {previous, current}:
        if not name:
            continue
        for key in PANEL_STATE.get(str(name), ()):
            reset[key] = None
    return reset
