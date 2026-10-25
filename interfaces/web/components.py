'卡 16：交互层组件（纯展示）—— 只渲染编排层给的东西，**不算任何业务数字**。'

from __future__ import annotations

import html
import re
from typing import Any, Callable, Mapping, Sequence

import streamlit as st

from interfaces.web.scenes import SCENES, SCENE_ICONS    # 场景文案拆出（本文件已贴 300 行红线）
from interfaces.web.theme import CSS, load_css
from interfaces.web import reply_animation
from interfaces.web import chart_ui, spending_pie

#: 顶部合规标注（卡 16 第 5 条）
SIM_NOTICE = "模拟环境 · 全部为合成数据：不连接任何真实银行，无真实用户 / 账户 / 资金 / 卡号。"

#: 侧边栏账户余额：同样只是把话交给编排层（界面不读库、不 import data/）。
#: 口径：只说「查一下余额」—— 编排层会取默认账户（储蓄）；一旦提到「储蓄卡/信用卡」，
#: 分类器会填中文槽位（'储蓄卡'/'credit_card'），而工具层的 `account_type` 只认 savings|credit，
#: 编排层没有这层归一化 → 工具直接 INVALID_ARGUMENT。这是 agent/ 层缺口，见交付说明「待拍板」。
BALANCE_PROMPT = "查一下余额"

def _esc(value: object) -> str:
    """HTML 转义（用户输入与回执都可能带尖括号）。"""
    return html.escape("" if value is None else str(value))


def _line(text: str, prefix: str) -> str:
    """从编排层文本里取以 `prefix` 开头的那一行（如确认卡的「风险提示：」）；取不到返回空串。"""
    for line in str(text).splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def sim_banner() -> None:
    st.markdown(f'<div class="sim-banner">{SIM_NOTICE}</div>', unsafe_allow_html=True)


def transcript(messages: Sequence[Mapping[str, Any]],
               *, payee_card: Callable[[str], bool] | None = None,
               insight_card: Callable[[Mapping[str, Any], str], bool] | None = None) -> None:
    '聊天记录：用户原话照原样，助手只回显编排层回执 + trace_id。'
    for index, message in enumerate(messages):
        turn = message.get("turn") or {}
        if (message["role"] == "assistant" and turn.get("intent") == "card_query"
                and turn.get("tool_calls") == ["list_bound_cards"]
                and (turn.get("facts") or {}).get("items")
                and not turn.get("ask") and not turn.get("error_code")):
            continue  # The bound-card gallery already presents this successful list.
        with st.chat_message(message["role"], avatar=":material/person:" if message["role"] == "user" else ":material/auto_awesome:"):
            turn = message.get("turn")
            drawn = (payee_card is not None and message["role"] == "assistant"
                     and (turn or {}).get("intent") == "payee_list"
                     and payee_card(message["text"]))
            if not drawn:
                if message["role"] == "assistant":
                    key = f"{index}:{(turn or {}).get('trace_id', 'greeting')}"
                    if not spending_pie.render_reply(message["text"], turn or {}, key=key, animate=index == len(messages) - 1):
                        reply_animation.render(message["text"], key, animate=index == len(messages) - 1)
                else:
                    st.markdown(message["text"])
            if turn is not None:
                with st.container(key=f"chat-audit-{index}"):
                    st.caption(f"trace_id `{turn['trace_id']}` · 意图 `{turn['intent']}` · "
                               f"档位 `{turn.get('tier') or '—'}` · 状态 {' → '.join(turn['states'])}")
                if insight_card is not None and message["role"] == "assistant" and index == len(messages) - 1:
                    insight_card(turn, f"insight-{index}")


def _activate_scene(index: int) -> None:
    st.session_state["active-scene"] = index


def scenes() -> str | None:
    """快捷场景按钮：返回被点的那句话（没点 → None）。"""
    clicked: str | None = None
    for index, (label, prompt) in enumerate(SCENES):
        active = st.session_state.get("active-scene") == index
        with st.container(key=f"scene-active-{index}" if active else f"scene-item-{index}"):
            if st.button(label, icon=SCENE_ICONS[index], key=f"scene-{index}",
                         width="stretch", on_click=_activate_scene, args=(index,)):
                clicked = prompt
    return clicked


def balance_box(replies: Sequence[Mapping[str, Any]]) -> bool:
    """侧边栏账户余额：只回显 agent 给过的余额回执（数字来自事实包）。返回是否点了刷新。"""
    if not replies:
        st.caption("暂无余额记录，点击刷新查询。")
    for item in replies[-1:]:
        facts = item.get('facts') or {}
        if facts.get('balance_yuan') is not None:
            label = {'savings': '储蓄账户', 'credit': '信用卡账户'}.get(facts.get('account_type'), '账户')
            st.markdown(f'<div class="bank-balance"><span class="bank-balance-label">您的{label}余额为：</span>'
                        f'<span class="bank-balance-amount">{_esc(facts["balance_yuan"])} RMB</span></div>',
                        unsafe_allow_html=True)
        else:
            st.markdown(re.split('[，。]', item['text'], maxsplit=1)[0].replace(' 元', ' RMB'))
    return st.button("刷新余额", icon=":material/refresh:", key="refresh-balance", width="stretch")


#: card-24 把 `Confirmation` 泛化后，卡面主对象字段是通用的 `target_name`（转账=收款人、订阅取消=商户）。
#: 这两张表给出每个写意图的卡面措辞；**表外意图按转账处理**（该模型最初就是为转账做的，行为不变）。
_CARD_TITLE = {"subscription_cancel": "订阅取消确认卡", "aa_collect": "AA 收款确认卡",
               "gift_plan": "送礼计划确认卡", "payee_whitelist": "白名单确认卡",
               "payee_blacklist": "黑名单确认卡", "payee_unblacklist": "解除黑名单确认卡",
               "payee_delete": "删除收款人确认卡",
               #: 2026-10-24 补齐：此前 `transfer_scheduled` 等新意图都落回默认的「转账确认卡」
               "transfer_scheduled": "定时转账确认卡",
               "scheduled_cancel": "取消预约确认卡", "scheduled_run": "立即执行预约确认卡",
               "scheduled_modify": "修改预约确认卡",
               "gift_order_cancel": "取消订单确认卡", "gift_order_pay": "支付订单确认卡",
               "gift_order_advance": "订单状态确认卡",
               "aa_mark_paid": "登记已付确认卡", "aa_cancel": "撤销 AA 确认卡",
               "aa_remind": "提醒付款确认卡",
               "payee_whitelist_edit": "修改白名单信息确认卡",
               "payee_whitelist_remove": "移出白名单确认卡",
               "payee_note": "修改备注确认卡", "payee_tags": "修改标签确认卡"}
_CARD_TARGET = {"subscription_cancel": "商户", "gift_plan": "联系人",
                "wealth_buy": "产品", "wealth_redeem": "产品"}
#: **只有这些意图**的卡有「金额」「预计到账」两截 —— 联系人状态类操作（加白/拉黑/删除）没有金额，
#: 否则会印出空的「金额（元）」和转账专用的「预计到账」，让白名单卡看起来像转账卡（实测缺陷）。
_MONEY_INTENTS = frozenset({"transfer_single", "transfer_scheduled", "subscription_cancel",
                            "aa_collect", "gift_plan", "wealth_buy", "wealth_redeem"})


def _aa_table(confirmation: Any) -> None:
    """AA 收款：主卡面就列**每人应收金额**的表格（一键均摊时每人相同、标注均摊）。

    数据全部来自确认卡快照（`filled`）—— 界面只渲染，不重算、不补数（铁律 2）。
    """
    filled = dict(getattr(confirmation, "filled", {}) or {})
    names = [str(name) for name in (filled.get("aa_names") or [])]
    if not names:
        return
    per = [int(cents) for cents in (filled.get("per_person_cents") or [])]
    if len(per) != len(names):                            # 一键均摊
        total = int(getattr(confirmation, "amount_cents", 0) or 0)
        per = [total // len(names)] * len(names)
        mode = "一键均摊"
    else:
        mode = "按人金额"
    rows = "\n".join(f"| {_esc(name)} | {cents / 100:.2f} |" for name, cents in zip(names, per))
    st.markdown(f"**每人应收（{mode}）**\n\n| 收款人 | 应收金额（元） |\n| --- | --- |\n{rows}")


def confirm_card(confirmation: Any) -> str | None:
    '确认卡**独立组件**（卡 16 第 2 条）：金额 / 卡面主对象 / 权限档(风险等级) / 确认 / 取消。'
    intent = str(confirmation.intent)
    title = _CARD_TITLE.get(intent, "转账确认卡")
    target = _CARD_TARGET.get(intent, "收款人")
    money = intent in _MONEY_INTENTS
    with st.container(border=True):
        st.markdown(f"##### 📋 {title}（等待您的确认）")
        head = st.columns(4)
        slot = 0
        if money:                                         # 只有涉及金额的意图才印金额
            head[slot].metric("金额（元）", confirmation.amount_yuan or "—")
            slot += 1
        head[slot].metric(target, confirmation.target_name or "—")
        head[slot + 1].metric("权限档（风险等级）", confirmation.tier or "—")
        head[slot + 2].metric("短信验证码", "需要" if confirmation.requires_otp else "不需要")
        parts = [f"意图：{_line(confirmation.card_text, '意图：') or confirmation.intent}"]
        if confirmation.masked_phone:                     # 转账/收款人才有
            parts.append(f"收款账号：{_esc(confirmation.masked_phone)}")
        if money:                                         # 「预计到账」是转账专用截
            parts.append(f"预计到账：{_line(confirmation.card_text, '预计到账：') or '—'}")
        st.caption(" · ".join(parts))
        if intent == "aa_collect":                        # AA：主卡面就列**每人应收**表格
            _aa_table(confirmation)
        if risk := _line(confirmation.card_text, "风险提示："):
            st.warning(f"风险提示：{risk}")
        left, right, _rest = st.columns([1, 1, 3])
        if left.button("✅ 确认", type="primary", key="confirm-ok", width="stretch"):
            return "confirm"
        if right.button("✖ 取消", key="confirm-cancel", width="stretch"):
            return "cancel"
    return None


def otp_card(confirmation: Any) -> tuple[str, str] | None:
    '等 OTP：只收集验证码，交编排层 → 工具层校验（界面不比对、不知晓正确值）。'
    target = _CARD_TARGET.get(str(confirmation.intent), "收款人")
    with st.container(border=True):
        st.markdown("##### 🔐 请输入短信验证码")
        st.caption(f"{target} {_esc(confirmation.target_name)}（{_esc(confirmation.masked_phone)}）· "
                   f"金额 {_esc(confirmation.amount_yuan)} 元 · 权限档 {_esc(confirmation.tier)}")
        with st.form("otp-form", clear_on_submit=True):
            code = st.text_input("短信验证码", type="password", key="otp-code",
                                 placeholder="请输入 6 位短信验证码", label_visibility="collapsed")
            left, right, _rest = st.columns([1, 1, 3])
            submitted = left.form_submit_button("提交验证码", type="primary", key="otp-submit",
                                               width="stretch")
            cancelled = right.form_submit_button("取消", key="otp-cancel", width="stretch")
        if cancelled:
            return ("cancel", "")
        if submitted:
            return ("submit", code)
    return None


def payee_form(defaults: dict | None = None) -> dict | None:
    """卡 20：添加收款人的表单（姓名 + 手机号 + **是否加入白名单**）。

    只**收集**输入并原样返回 `(name, phone, whitelist)`；未提交 → `None`。校验 / 脱敏 / 落库 /
    审计一律在 `agent/` → `tools/`（界面不写业务逻辑）。
    `defaults`：聊天句里已说出的姓名/手机号 → 预填（来自 `agent/payee_flow.pending_slots`）。

    ⚠️ 勾「同时加入白名单」**不等于**当场授予特权：界面只把意图传下去，真正的授予要走
    **L2 确认卡 + 短信验证码**（`agent/payee_flow.py` → `write_flow` → T25）——
    白名单 = 转账免验证码的特权，不能靠一次表单点击就拿到。
    """
    defaults = defaults or {}
    with st.container(border=True):
        st.markdown("##### ➕ 添加收款人")
        st.caption("填姓名和手机号即可（不收卡号）。手机号会脱敏保存，完整号码不入库、不回显。")
        with st.form("payee-form", clear_on_submit=True):
            name = st.text_input("姓名", key="payee-name", placeholder="例如：王小明",
                                 value=str(defaults.get("name") or ""))
            phone = st.text_input("手机号", key="payee-phone", placeholder="11 位数字，例如 13812345678",
                                  value=str(defaults.get("phone") or ""))
            card_no = st.text_input("银行卡号（可空）", key="payee-card-no",
                                    placeholder="只保存脱敏形态（**** **** **** 8888）")
            tags = st.text_input("标签（可空，多个用、分隔；**能用来指代这个人**）", key="payee-tags",
                                 placeholder="例如：妈妈、房东（之后可以说「给我妈转 200」）")
            note = st.text_input("备注（可空；**只给自己看，不参与识别**）", key="payee-note",
                                 placeholder="例如：老同学，别转错",
                                 value=str(defaults.get("note") or ""))
            whitelist = st.checkbox("同时加入白名单（之后转账免验证码，需再输一次验证码）",
                                    key="payee-whitelist")
            quota = ""
            expire = ""
            if whitelist:                                    # 2026-10-24：加白要设额度与有效期
                col_a, col_b = st.columns(2)
                quota = col_a.text_input("专属额度（元/月）", key="payee-quota",
                                         placeholder="例如 5000（不填 = 一律要验证码）")
                expire = col_b.text_input("有效期至", key="payee-expire",
                                          placeholder="例如 2026-12-31 或 月底（不填 = 长期）")
            submitted = st.form_submit_button("添加收款人", type="primary", width="stretch")
        if submitted:
            return {"name": name, "phone": phone, "whitelist": bool(whitelist),
                    "note": note, "quota": quota, "expire": expire, "tags": tags,
                    "card_no": card_no}
    return None


def pending_card(text: str) -> bool:
    """L3 待复核卡：只给撤销入口（撤销本身走 agent/confirm_card，界面不改状态）。"""
    with st.container(border=True):
        st.markdown("##### ⏳ 这笔转账待人工复核（L3）")
        st.markdown(text)
        return st.button("↩️ 撤销这笔转账", key="pending-revoke")


# ---------------- 图表：从编排层回执解析坐标点 ----------------

#: 三种回执形状（全部由 agent/templates 与工具层 markdown 生成，这里只认它们）
_REPORT_HEAD = re.compile(r"(?P<period>\d{4}(?:-\d{2})?) 账单：支出 (?P<out>[\d,]+\.\d{2}) 元")
_ANALYSIS_TOTAL = re.compile(r"(?P<period>\d{4}(?:-\d{2})?) 共支出 (?P<out>[\d,]+\.\d{2}) 元")
_CATEGORY_ROW = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<amount>[\d,]+\.\d{2})\s*\|\s*(?P<pct>\d+)%\s*\|$")


def _yuan(text: str) -> float:
    """展示用数值：把回执里的「元」串转成图表坐标（纯格式化，不重算金额）。"""
    return float(str(text).replace(",", ""))


def parse_bill(text: str) -> dict:
    """回执 → 图表数据 `{period, total, categories:[{key, amount, pct}]}`；解析不出返回 `{}`。"""
    parsed: dict[str, Any] = {"period": None, "total": None, "categories": []}
    for line in str(text).splitlines():
        match = _REPORT_HEAD.search(line) or _ANALYSIS_TOTAL.search(line)
        if match is not None:
            parsed["period"], parsed["total"] = match.group("period"), _yuan(match.group("out"))
        elif (row := _CATEGORY_ROW.match(line)) is not None:
            parsed["categories"].append({"key": row.group("key"), "amount": _yuan(row.group("amount")),
                                         "pct": int(row.group("pct"))})
    return parsed if parsed["total"] is not None else {}


def bill_series(messages: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """会话里所有账单回执 → `{账期: 支出合计}`（趋势图每个点都来自一次真实回执）。"""
    series: dict[str, float] = {}
    for message in messages:
        if message["role"] != "assistant":
            continue
        parsed = parse_bill(message["text"])
        if parsed:
            series[str(parsed["period"])] = float(parsed["total"])
    return dict(sorted(series.items()))


def latest_bill(messages: Sequence[Mapping[str, Any]]) -> dict:
    """最后一条可解析的账单回执（分类表来自它）。"""
    for message in reversed(list(messages)):
        if message["role"] == "assistant" and (parsed := parse_bill(message["text"])):
            return parsed
    return {}


def bill_panel(series: Mapping[str, float], latest: Mapping[str, Any]) -> None:
    """分类已随聊天回复展示；此处只保留跨月趋势。"""
    st.markdown("**月度趋势（支出合计）**")
    if series:
        chart_ui.trend(series)
    else:
        st.caption("还没有账期数据。")


# ---------------- 审计时间轴 ----------------

def timeline(records: Sequence[Mapping[str, Any]]) -> None:
    """审计时间轴（卡 16 第 4 条）：按 trace_id 展开「意图 → 工具 → 权限 → 结果」。"""
    if not records:
        st.info('还没有请求。直接聊天或点一个快捷场景即可开始。')
        return
    st.caption("每次请求编排层都写一条 audit_log（铁律 5）；下面按**同一条请求**的 trace_id "
               "展开编排层轨迹：意图 → 槽位 → 权限 → 工具 → 结果。")
    for record in reversed(records):
        turn = record["turn"]
        with st.expander(f"{record['ts']} · {turn['intent']} · 「{record['text'][:22]}」",
                         expanded=len(records) == 1):
            st.markdown(_steps_html(turn, record["text"]), unsafe_allow_html=True)


def _steps_html(turn: Mapping[str, Any], text: str) -> str:
    """一条请求的时间轴 HTML（每个字段都直接来自 `Turn`，界面不解释）。"""
    steps = [("用户输入", text),
             ("意图识别", f"{turn['intent']}（置信度 {turn['confidence']}）"),
             ("缺槽检查", "、".join(turn["missing_slots"]) or "无"),
             ("权限判定", turn.get("tier") or "未定档（只读恒 L0；拒答与未接通不定档）"),
             ("工具调用", "、".join(turn["tool_calls"]) or "无"),
             ("执行结果", f"executed={turn['executed']} · error_code={turn.get('error_code') or '—'}"),
             ("回执", turn["reply"])]
    items = "".join(f'<li><span class="dot"></span><div class="k">{_esc(key)}</div>'
                    f'<div class="v">{_esc(value)}</div></li>' for key, value in steps)
    chips = "".join(f'<span class="chip">{_esc(state)}</span>' for state in turn["states"])
    return (f'<ul class="tl">{items}</ul>'
            f'<div class="tl-meta">trace_id {_esc(turn["trace_id"])} · 状态轨迹 {chips}</div>')
