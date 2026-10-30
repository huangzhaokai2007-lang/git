"""群收款卡（2026-10-29 三卡合一）：参考微信「群收款」的单页形态。

一屏之内：金额 → 参与人（多选/全选/确认）→ 备注 → 均摊金额（大字）→ 按人金额 → **确认/发起**。
合规链路不变（预览 → 确认 → 执行），只是把"确认"搬进同一张卡：
- 没有在途确认 → 显示发起表单；
- 有在途确认（`confirm_card.current`）→ 同一张卡里显示卡面摘要 + 「✅ 确认 / ✖ 取消 / 改金额」。

分层：只调 `agent/`（`aa_flow` 出数与名单、`write_flow` 走写流程、`confirm_card` 读在途）。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import aa_flow, confirm_card, payee_flow, payee_picker, write_flow

import premium_ui
import step_record


def _record(step: write_flow.Step, summary: str) -> None:
    """按钮/卡面路径的回执记账（含 L3 的 pending_id，否则待复核卡不显示）。"""
    step_record.record_step(step, summary)


def _start(executor: ThreadPoolExecutor, session_id: str, intent: str,
           slots: dict, display: str) -> None:
    from agent import pending_slots

    confirm_card.clear(session_id)
    #: 卡面点「发起群收款」= 用户已看过编辑卡 → 直接进预检/确认（不再绕回编辑卡）
    step = executor.submit(write_flow.start, intent, slots, session_id,
                           from_card=True).result()
    #: 2026-10-30 修（用户实测「成功发起后卡面不消失」）：出确认卡 / 已发起 → 清掉补槽记录，
    #: 否则下一轮 `pending_slots` 里那条 aa_collect 会把发起表单**又自动展开**并回填旧值。
    if not getattr(step, "missing", None):
        pending_slots.clear(session_id)
    _record(step, display)
    st.rerun()


def _say(executor: ThreadPoolExecutor, session_id: str, text: str, summary: str) -> None:
    """把一句话交给编排层（按钮 = 打字），然后 rerun。"""
    from agent import orchestrator

    turn = executor.submit(orchestrator.handle, text, session_id=session_id).result()
    state = st.session_state
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": summary, "turn": None})
    if getattr(turn, "executed", False):
        #: 办完了 → 下一轮开头把「新增群收款」表单整个清掉（不留上次的残余），
        #: 同时清掉补槽记录 —— 否则下轮会被它重新展开（用户实测的"卡面不消失"）。
        from agent import pending_slots

        pending_slots.clear(session_id)
        state["aa-reset-pending"] = True
    st.rerun()


def _normalize_candidates(raw: list) -> dict[str, dict]:
    """候选 → `{id: {id, name, label}}`。**两种形状都接受**：

    - 新形状：`{"id", "name", "label"}`；
    - 老形状：纯字符串（姓名）。

    兜底是为了不让一次模块缓存/版本错配把整页搞红（实测红过一次：
    `TypeError: string indices must be integers`）。
    """
    catalog: dict[str, dict] = {}
    for index, item in enumerate(raw or []):
        if isinstance(item, dict):
            payee_id = str(item.get("id") or item.get("payee_id") or f"unknown-{index}")
            name = str(item.get("name") or payee_id)
            label = str(item.get("label") or name)
        else:
            name = str(item)
            payee_id = f"name-{index}-{name}"
            label = name
        catalog[payee_id] = {"id": payee_id, "name": name, "label": label}
    return catalog


def _to_yuan(value: object) -> float:
    """把用户输入的金额文本转成元（认不出就当 0）。"""
    try:
        return float(str(value or "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _sync_total() -> None:
    """任一按人金额（含「我承担」）改动 → 总额自动重算：`Σ其他人 + 我承担`。

    在 `on_change` 里调用（回调先于脚本重跑执行，所以能安全改 `aa-total` 这个控件状态）。
    """
    others = sum(_to_yuan(value) for key, value in list(st.session_state.items())
                 if str(key).startswith("aa-amt-"))
    mine = _to_yuan(st.session_state.get("aa-mine"))
    st.session_state["aa-amount-touched"] = True      # 用户动过按人金额 → 不再自动对齐均摊值
    #: 2026-10-31 修（用户实测：点「按份数均摊」后总金额被清零）——
    #: 输入框还空着 / 都是 0 时**不要**拿 0 去覆盖已有总额。
    total_now = round(others + mine, 2)
    if total_now <= 0:
        return
    st.session_state["aa-total"] = total_now


def _split_count() -> int:
    """当前参与人个数（不含我）。"""
    return len([key for key in st.session_state if str(key).startswith("aa-amt-")])


def _write_amounts(values: list[float], mine: float) -> None:
    """把算好的金额写回输入框（整数分落库口径，避免浮点尾巴）。"""
    for index, value in enumerate(values):
        st.session_state[f"aa-amt-{index}"] = f"{value:.2f}"
    st.session_state["aa-mine"] = f"{mine:.2f}"
    st.session_state["aa-total"] = round(sum(values) + mine, 2)
    st.session_state["aa-amount-touched"] = True     # 这是一次"用户设定"，别被默认值对齐覆盖


def _even_split() -> None:
    """一键均摊：全员（含我）拉回平均。"""
    total_cents = int(round(_to_yuan(st.session_state.get("aa-total")) * 100))
    count = _split_count() + 1
    if total_cents <= 0 or count <= 0:
        return
    #: 用整数分算：别人各拿 floor 的那一份，**零头记在我这边** —— 合计不会变成 999.99。
    per = total_cents // count
    mine = total_cents - per * (count - 1)
    _write_amounts([per / 100] * (count - 1), mine / 100)


def _apply_weights() -> None:
    """按份数均摊：每份 = 总额 ÷ 总份数；余下的零头记在我这边（不外溢）。"""
    total_cents = int(round(_to_yuan(st.session_state.get("aa-total")) * 100))
    others = [max(1, int(st.session_state.get(f"aa-weight-{i}") or 1))
              for i in range(_split_count())]
    mine_weight = max(1, int(st.session_state.get("aa-weight-mine") or 1))
    shares = sum(others) + mine_weight
    if total_cents <= 0 or shares <= 0:
        return
    unit = total_cents // shares
    cents = [unit * weight for weight in others]
    mine_cents = unit * mine_weight + (total_cents - unit * shares)
    _write_amounts([value / 100 for value in cents], mine_cents / 100)
    st.session_state["aa-weight-open"] = False


def _align_amount_defaults(people: list[str], plan: dict) -> None:
    """用户还没动过按人金额时，把它对齐到**当前均摊值**。

    2026-10-31 修（用户截图里明细全是 0.00）：总额是后填的（先加人、再填 490），
    而金额输入框第一次渲染时落了 0.00；这里在**渲染显示区之前**按当前均摊值刷新，
    用户一旦手改过（`aa-amount-touched`）就不再覆盖。
    """
    signature = "|".join(people)
    if st.session_state.get("aa-amount-signature") != signature:
        st.session_state["aa-amount-signature"] = signature
        st.session_state["aa-amount-touched"] = False
    if st.session_state.get("aa-amount-touched"):
        return
    for index in range(len(people)):
        st.session_state[f"aa-amt-{index}"] = plan["per_yuan"]
    #: 我那份用工具层算好的 mine_yuan（含零头）—— 否则 3 个人各 163.33 合计只有 489.99
    st.session_state["aa-mine"] = plan.get("mine_yuan") or plan["per_yuan"]


def _toggle(key: str) -> None:
    st.session_state[key] = not st.session_state.get(key, False)


def _remember_people(picked: list[str]) -> None:
    st.session_state["aa-people"] = list(picked)
    st.session_state["aa-people-touched"] = True      # 用户动过名单 → 不再自动预填
    st.session_state["aa-pick-open"] = False


def _add_selected(picked: list[str]) -> None:
    """「添加所选」：把多选里挑出来的人并进参与人（去重）。"""
    current = list(st.session_state.get("aa-people") or [])
    st.session_state["aa-people"] = current + [n for n in picked if n not in current]
    st.session_state["aa-people-touched"] = True
    st.session_state["aa-picked"] = []                      # 回调里清，下次渲染前生效
    st.session_state["aa-people-open"] = False              # 2026-10-31：加完就收起这块


def _remove_person(name: str) -> None:
    """「删除」某位参与人。"""
    st.session_state["aa-people"] = [p for p in (st.session_state.get("aa-people") or [])
                                     if p != name]
    st.session_state["aa-people-touched"] = True


def _collapse_people() -> None:
    """把「修改参与人」收起（用户 2026-10-31：点完添加所选这块该自己收起来）。"""
    st.session_state["aa-people-open"] = False


def _select_all() -> None:
    """「全选」：把候选里还没加的人一次加进来。"""
    current = list(st.session_state.get("aa-people") or [])
    st.session_state["aa-people"] = current


def _confirm_panel(executor: ThreadPoolExecutor, session_id: str, confirmation) -> None:
    """在途确认：在同一张卡里给「确认 / 取消 / 改金额」。"""
    filled = dict(getattr(confirmation, "evidence", None) or {})
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款 · 确认这一笔",
                             "看一眼再确认；确认后才会真的发出去", tone="warn")
        st.markdown(f"**金额（元）**　{getattr(confirmation, 'amount_cents', 0) / 100:,.2f}")
        st.markdown(f"**收款人**　{filled.get('target_name') or '—'}")
        #: 2026-10-31（用户要求）：**逐人列出每人该承担多少**，不再挤成一句「每人 —」。
        items = list(filled.get("per_person_items") or [])
        if items:
            table = ["| 收款人 | 应收（元） |", "| --- | --- |"]
            table += [f"| {item.get('name') or '—'} | {item.get('yuan') or '0.00'} |"
                      for item in items]
            st.markdown("\n".join(table))
        st.markdown(f"**我承担**　{filled.get('initiator_share_yuan', '—')} 元")
        st.markdown(f"**待收**　{filled.get('collect_yuan', '—')} 元")
        left, middle, right = st.columns(3, vertical_alignment="center")
        #: 2026-10-29 修（用户实测「点击确认后直接卡 bug」）：这里原来调的是**不存在的**
        #: `write_flow.confirm` / `write_flow.cancel` → AttributeError。
        #: 正确做法是与聊天里说「确认 / 取消」完全同一条路径：交给编排层。
        if left.button("✅ 确认", key="aa-confirm-go", type="primary", width="stretch"):
            _say(executor, session_id, "确认", "（按钮）确认群收款")
        if middle.button("✖ 取消", key="aa-confirm-cancel", width="stretch"):
            _say(executor, session_id, "取消", "（按钮）取消群收款")
        amount_new = right.text_input("改金额（元）", key="aa-confirm-amount",
                                      label_visibility="collapsed", placeholder="改金额")
        if amount_new.strip() and right.button("按新金额重算", key="aa-confirm-edit",
                                               width="stretch"):
            step = executor.submit(write_flow.edit, session_id,
                                   {"amount": amount_new.strip()}).result()
            _record(step, "（群收款金额已修改）")
            st.rerun()


def _preset(executor: ThreadPoolExecutor, session_id: str) -> dict:
    """把用户**上一句已经说过的**人/金额/事由带进卡面（避免"还没选参与人"）。

    2026-10-29（用户实测）：「向李四、张小美、王五群收款」→ 系统追问总金额，
    但卡面参与人却是空的，得重新点选一遍。人明明已经解析出来了，就该直接带出来。
    """
    from agent import pending_slots

    item = executor.submit(pending_slots.peek, session_id).result() or {}
    slots = dict(item.get("slots") or {})
    names = [str(name) for name in (slots.get("aa_names") or []) if str(name).strip()]
    #: 已解析成 id 的名单：卡面优先用它预填（同名也唯一）
    ids = [str(pid) for pid in (slots.get("payee_ids") or []) if str(pid).startswith("payee_")]
    if not names and slots.get("payee_ids"):
        names = [str(pid) for pid in slots["payee_ids"]]
    amount = slots.get("amount")
    try:
        total = float(str(amount)) if amount not in (None, "") else 0.0
    except ValueError:
        total = 0.0
    return {"names": names, "ids": ids, "total": total,
            "title": str(slots.get("title") or "")}


def _create_panel(executor: ThreadPoolExecutor, session_id: str) -> None:
    """发起表单：金额 / 参与人 / 备注 / 均摊 / 按人金额 / 发起。"""
    preset = _preset(executor, session_id)
    if (preset["names"] and not st.session_state.get("aa-people")
            and not st.session_state.get("aa-people-touched")):
        #: 预填（2026-10-30 修）：**优先用已解析出来的 payee_id**（`preset["ids"]`，
        #: 同名两位也唯一确定）；没有 id 时才退回按姓名映射，且只在姓名唯一时采用。
        catalog_now = list(_normalize_candidates(
            executor.submit(aa_flow.candidates).result()).values())
        known = {item["id"] for item in catalog_now}
        preset_ids = [pid for pid in preset.get("ids", []) if pid in known]
        if not preset_ids:
            for name in preset["names"]:
                matches = [item["id"] for item in catalog_now if item["name"] == name]
                if len(matches) == 1:
                    preset_ids.append(matches[0])
        if preset_ids:
            st.session_state["aa-people"] = preset_ids
            st.session_state["aa-picked"] = preset_ids
    if preset["total"] and not st.session_state.get("aa-total"):
        st.session_state["aa-total"] = preset["total"]
    if preset["title"] and not st.session_state.get("aa-title"):
        st.session_state["aa-title"] = preset["title"]
    #: 2026-10-29（用户实测「这又是什么」）：同名多命中时上方已经出了**解析卡**，
    #: 这里再画一张"还没有确认参与人"的群收款卡，两张叠着很怪 —— 等用户点完解析卡再说。
    if executor.submit(payee_picker.pending, session_id).result():
        return
    with st.container(border=True, key="aa-create"):
        premium_ui.card_head("🧾 群收款（含你自己）",
                             "填金额 → 选参与人 → 发起；每人均摊，你承担自己那一份", tone="")
        #: 2026-10-31（用户要求）：总金额与备注 **1:1**（原来 [1, 2]，金额框显得太窄）
        money_col, title_col = st.columns([1, 1], vertical_alignment="bottom")
        total = money_col.number_input("总金额（元）", min_value=0.0, step=10.0, key="aa-total",
                                       format="%.2f")
        title = title_col.text_input("备注（事由）", key="aa-title", placeholder="例如 出去吃饭")

        catalog = _normalize_candidates(executor.submit(aa_flow.candidates).result())
        people = [pid for pid in (st.session_state.get("aa-people") or [])]
        labels = [catalog.get(pid, {}).get("label") or str(pid) for pid in people]
        plan = executor.submit(aa_flow.group_plan, labels, float(total or 0)).result()
        #: 2026-10-30（用户要求）：参与人要**醒目** —— 人数徽章 + 名字芯片，
        #: 不再是一行灰色小字。
        #: 2026-10-31（用户要求）：**参与人与每人应摊表合并成一块**，配色也调过（不再是灰表格）。
        _align_amount_defaults(people, plan)
        _per = float(plan["per_yuan"])
        _values = [_to_yuan(st.session_state[f"aa-amt-{i}"])
                   if f"aa-amt-{i}" in st.session_state else _per
                   for i in range(len(people))]
        _others = sum(_values)
        _mine = (_to_yuan(st.session_state["aa-mine"])
                 if "aa-mine" in st.session_state else _per)
        if people:
            chips = "".join(f'<span class="aa-chip">{label}</span>' for label in labels)
            rows_html = "".join(
                f'<div class="aa-split-row"><span>{label}</span>'
                f'<span class="aa-split-amt">¥ {value:,.2f}</span></div>'
                for label, value in zip(labels, _values))
            rows_html += ('<div class="aa-split-row me"><span>我（承担）</span>'
                          f'<span class="aa-split-amt">¥ {_mine:,.2f}</span></div>')
            rows_html += ('<div class="aa-split-row total"><span>合计</span>'
                          f'<span class="aa-split-amt">¥ {_others + _mine:,.2f}</span></div>')
            st.markdown(
                f'<div class="aa-people aa-merged"><div class="aa-people-head">'
                f'<span class="aa-people-label">参与人</span>'
                f'<span class="aa-people-count">{plan["count"]} 人（含我）</span></div>'
                f'<div class="aa-chips">{chips}</div>'
                f'<div class="aa-split">{rows_html}</div>'
                f'<div class="aa-split-foot"><span>待收 ¥{_others:,.2f}</span>'
                f'<span>均摊 ¥{plan["per_yuan"]}</span></div></div>',
                unsafe_allow_html=True)
        else:
            st.markdown('<div class="aa-people"><div class="aa-people-head">'
                        '<span class="aa-people-label">参与人</span></div>'
                        '<div class="aa-empty">还没有参与人 —— 点下面「✏️ 修改参与人」添加</div>'
                        '</div>', unsafe_allow_html=True)

        #: 参与人的增删收进这块（不再占主视野）；2026-10-31 用户要求：
        #: 「点添加所选之后这块可以收起」→ 用按钮控制展开态（expander 的状态改不动）。
        _people_open = bool(st.session_state.get("aa-people-open"))
        if st.button("✏️ 收起修改参与人" if _people_open else "✏️ 修改参与人",
                     key="aa-people-toggle", width="stretch"):
            st.session_state["aa-people-open"] = not _people_open
            st.rerun()
        if _people_open:
            if people:
                for index, pid in enumerate(people):
                    row_left, row_right = st.columns([5, 1], vertical_alignment="center")
                    row_left.markdown(f"**{catalog.get(pid, {}).get('label') or pid}**")
                    if row_right.button("删除", key=f"aa-rm-{index}-{pid}", width="stretch"):
                        _remove_person(pid)
                        st.rerun()
            else:
                st.caption("还没有参与人 —— 从下面挑，或新增一个不在名单里的人。")
            options = [pid for pid in catalog if pid not in people]
            picked = st.multiselect("添加参与人（可搜索、可多选）", options, key="aa-picked",
                                    format_func=lambda pid: catalog.get(pid, {}).get("label") or pid,
                                    placeholder="选择要添加的人")
            col_add, col_all = st.columns(2)
            col_add.button("添加所选", key="aa-add-sel", width="stretch",
                           disabled=not picked, on_click=_add_selected, args=(picked,))
            col_all.button("全选（把候选都加进来）", key="aa-add-all", width="stretch",
                           on_click=_remember_people, args=(list(catalog),))
            with st.expander("＋ 新增收款人（不在列表里的人）", expanded=False):
                new_name = st.text_input("姓名", key="aa-new-name")
                new_phone = st.text_input("手机号", key="aa-new-phone", placeholder="138****0001")
                if st.button("添加并计入参与人", key="aa-add-person",
                             disabled=not (new_name.strip() and new_phone.strip())):
                    turn = executor.submit(payee_flow.submit_payee, new_name.strip(),
                                           new_phone.strip(), session_id=session_id).result()
                    if getattr(turn, "executed", False) or "已添加" in (turn.reply or ""):
                        fresh = _normalize_candidates(
                            executor.submit(aa_flow.candidates).result()).values()
                        new_id = next((item["id"] for item in fresh
                                       if item["name"] == new_name.strip()), None)
                        merged = list(st.session_state.get("aa-people") or [])
                        if new_id and new_id not in merged:
                            merged.append(new_id)
                        st.session_state["aa-people"] = merged
                    st.rerun()

        if not people:
            st.caption("选好参与人后这里会显示均摊金额。")
            return
        amounts: dict[str, str] = {}
        #: 2026-10-31（用户：这两个按钮样式不一样）：和「修改参与人」统一成**按钮 + 展开区**
        _amounts_open = bool(st.session_state.get("aa-amounts-open"))
        if st.button("✏️ 收起按人设置金额" if _amounts_open else "✏️ 按人设置金额（可选）",
                     key="aa-amounts-toggle", width="stretch"):
            st.session_state["aa-amounts-open"] = not _amounts_open
            st.rerun()
        if _amounts_open:
            st.caption("默认是当前均摊值；改谁就动谁，**总额会跟着自动变**（总额 = 其他人合计 + 我承担）。")
            #: 2026-03-31：签名只跟**参与人**走 —— 改总额不该把用户已填的金额冲掉。
            for index, pid in enumerate(people):
                #: 标签用人话（李四（139****1001）），键仍用 payee_id（同名不会串）
                label = catalog.get(pid, {}).get("label") or pid
                amounts[pid] = st.text_input(f"{label} 应收（元）", key=f"aa-amt-{index}",
                                             on_change=_sync_total)
            #: 2026-10-31（用户要求）：按人编辑里**加上"我"** —— 否则改完其他人金额，
            #: 我那份还是老值，看起来像"均摊份额没变化"。改动会经 _sync_total 反推总额。
            mine_value = st.text_input("我承担（元）", key="aa-mine", on_change=_sync_total)

            #: 两个快捷（用户 2026-10-31 要求）：一键均摊 / 按份数均摊
            col_even, col_weight = st.columns(2)
            col_even.button("一键均摊", key="aa-even", width="stretch", on_click=_even_split)
            col_weight.button("按份数均摊", key="aa-weight-toggle", width="stretch",
                              on_click=_toggle, args=("aa-weight-open",))
            if st.session_state.get("aa-weight-open"):
                st.caption("每份 = 总额 ÷ 总份数（份数可以不同，例如 1 / 2 / 2）。")
                for index, pid in enumerate(people):
                    label = catalog.get(pid, {}).get("label") or pid
                    st.number_input(f"{label} 份数", key=f"aa-weight-{index}",
                                    min_value=1, max_value=20, step=1, value=1)
                st.number_input("我 份数", key="aa-weight-mine",
                                min_value=1, max_value=20, step=1, value=1)
                col_ok, col_no = st.columns(2)
                col_ok.button("确认按份数均摊", key="aa-weight-apply", type="primary",
                              width="stretch", on_click=_apply_weights)
                col_no.button("取消", key="aa-weight-cancel", width="stretch",
                              on_click=_toggle, args=("aa-weight-open",))

        if st.button("发起群收款", key="aa-create-go", type="primary", width="stretch",
                     disabled=float(total or 0) <= 0):
            slots = {"payee_ids": people, "amount": plan["total_yuan"]}   # people 已是 payee_id
            if title.strip():
                slots["title"] = title.strip()
            #: 2026-10-30 修：默认值就是均摊值，**不要**当"按人金额"发下去 ——
            #: 含我口径下 Σ每人 ≠ 含我总额，会被判「每人金额合计与总金额不符」，
            #: 卡面停在发起态 → 用户再点一次又会触发收款人解析（用户实测的"还是会解析"）。
            filled = {name: value.strip() for name, value in amounts.items()
                      if value.strip() and value.strip() != plan["per_yuan"]}
            if filled:
                slots["amounts"] = "、".join(f"{name} {value}" for name, value in filled.items())
            _start(executor, session_id, "aa_collect", slots,
                   "（按钮）发起群收款：" + "、".join(people))


def _drain_paid_queue(executor: ThreadPoolExecutor, session_id: str) -> None:
    """把排队中的「登记已付」一个一个接上（当前没有在途确认卡时才接下一个）。"""
    from agent import confirm_card as _cc

    queue = list(st.session_state.get("aa-paid-queue") or [])
    if not queue:
        return
    if executor.submit(_cc.current, session_id).result() is not None:
        return                                   # 还有确认卡没处理，等用户
    next_item = queue.pop(0)
    st.session_state["aa-paid-queue"] = queue
    step = executor.submit(write_flow.start, "aa_mark_paid",
                           {"request_id": next_item["request_id"],
                            "payee_id": next_item["payee_id"]}, session_id).result()
    _record(step, f"（队列）登记 {next_item['payee_id']} 已付")
    st.rerun()


def _status_card(executor: ThreadPoolExecutor, session_id: str, item: dict) -> None:
    """付款状态卡：谁已付 / 谁没付 + 登记已付 + 撤销。"""
    request_id = str(item["request_id"])
    with st.container(border=True, key=f"aa-status-{request_id}"):
        premium_ui.card_head(f"🧾 {item['title']}",
                             f"待收 {item.get('collect_yuan', item['total_yuan'])} 元 · "
                             f"已付 {item['paid_count']}/{item['share_count']} 人 · "
                             f"截止 {item['expire_at']}", tone="ok")
        for share in list(item.get("shares") or []):
            done = share["status"] == "paid"
            st.markdown(f"{'✅' if done else '⏳'} **{share['payee_name']}** · "
                        f"{share['amount_yuan']} 元 · {'已付' if done else '未付'}")
        pending = [share for share in (item.get("shares") or []) if share["status"] != "paid"]
        #: 2026-10-31（用户要求）：登记已付改成**勾选式** —— 点按钮放出未付的人，
        #: 勾谁付了（可多选）+ 确认/取消，不再一个一个地下拉选。
        open_key = f"aa-paid-open-{request_id}"
        gen_key = f"aa-paid-gen-{request_id}"
        #: 2026-10-31 修（浏览器红屏 `StreamlitWidgetAlreadyInstantiatedError`）：
        #: 勾选框用**代际 key**，开/关一次就换一代，旧 key 自然作废 —— 不用去 set/pop widget key。
        picked_key = f"aa-paid-pick-{request_id}-{int(st.session_state.get(gen_key) or 0)}"
        col_pay, col_cancel = st.columns(2, vertical_alignment="center")
        if pending:
            label = "✏️ 收起登记" if st.session_state.get(open_key) else "登记已付"
            if col_pay.button(label, key=f"aa-paid-{request_id}", type="primary",
                              width="stretch"):
                st.session_state[open_key] = not st.session_state.get(open_key)
                st.session_state[gen_key] = int(st.session_state.get(gen_key) or 0) + 1
                st.rerun()
        else:
            col_pay.caption("这笔群收款已全部付清 ✅")
        if col_cancel.button("撤销", key=f"aa-cancel-{request_id}", width="stretch"):
            _start(executor, session_id, "aa_cancel", {"request_id": request_id},
                   f"（按钮）撤销群收款 {item['title']}")

        if pending and st.session_state.get(open_key):
            options = {f"{share['payee_name']}（{share['amount_yuan']} 元）": share["payee_id"]
                       for share in pending}
            picked = st.multiselect("勾选已经付给你的人", list(options),
                                    key=picked_key,
                                    placeholder="勾选已经付了的人")
            col_ok, col_no = st.columns(2)
            if col_ok.button("确认登记", key=f"aa-paid-ok-{request_id}", type="primary",
                             width="stretch", disabled=not picked):
                #: 工具层一次登记一个人（各自走确认卡）→ 勾了多位就排队，
                #: 当前这个先发起，其余等确认完自动接上（见 `_drain_paid_queue`）。
                rest = [{"request_id": request_id, "payee_id": options[who]}
                        for who in picked[1:]]
                st.session_state["aa-paid-queue"] = rest
                st.session_state[open_key] = False
                #: 勾选框是**已实例化**的 widget，这里不能直接清 → 挂标记，下一轮开头清
                st.session_state[gen_key] = int(st.session_state.get(gen_key) or 0) + 1
                _start(executor, session_id, "aa_mark_paid",
                       {"request_id": request_id, "payee_id": options[picked[0]]},
                       f"（按钮）登记 {picked[0]} 已付")
            if col_no.button("取消", key=f"aa-paid-no-{request_id}", width="stretch"):
                st.session_state[open_key] = False
                st.session_state[gen_key] = int(st.session_state.get(gen_key) or 0) + 1
                st.rerun()


#: 群收款办完后要清掉的表单状态（含 widget key，只能下一轮开头清）
_FORM_KEYS = ("aa-create-open", "aa-people", "aa-people-touched", "aa-picked",
              "aa-total", "aa-title", "aa-pick-open", "aa-split-open")


def _reset_form() -> None:
    """把「新增群收款」表单清空（用户 2026-10-31：重新发起时金额还留着上一次的 390）。

    点「＋ 新增群收款」= 明确要**新起一笔**，所以金额/备注/参与人/按人金额一律归零；
    上一笔还没填完就切走的情况也走这里，避免旧数字串到下一笔。
    """
    for key in list(st.session_state):
        name = str(key)
        if name in _FORM_KEYS or name.startswith(("aa-amt-", "aa-weight-", "aa-amount-")):
            st.session_state.pop(key, None)
    for key in ("aa-mine", "aa-total", "aa-title"):
        st.session_state.pop(key, None)


def _apply_pending_reset() -> None:
    """延后清理：上一轮结束时挂了 `aa-reset-pending` → 这一轮开头把表单清干净。

    Streamlit 不允许在同一轮里改**已实例化** widget 的 session_state，
    所以「办完就收起」必须放到下一轮的最前面做。
    """
    if not st.session_state.pop("aa-reset-pending", False):
        return
    _reset_form()


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """一张卡（发起或在途确认）+ 每笔付款状态卡。

    2026-10-29（用户要求）：查看群收款时**不再直接铺开**发起表单 ——
    先给一个「＋ 新增群收款」按钮，点了才拉开；已经在补槽（例如刚说"向王五群收款"缺金额）时自动展开。
    """
    _apply_pending_reset()
    if st.session_state.pop("aa-reset-form-pending", False):
        _reset_form()                     # 新的一句「我要 aa 收款」→ 表单归零
    _drain_paid_queue(executor, session_id)
    confirmation = executor.submit(confirm_card.current, session_id).result()
    if confirmation is not None and getattr(confirmation, "intent", "") == "aa_collect":
        st.session_state["aa-create-open"] = False       # 进了确认态就收起发起表单
        st.session_state["aa-reset-pending"] = True      # 并且把上一轮填的内容清掉
        _confirm_panel(executor, session_id, confirmation)
    else:
        from agent import pending_slots

        item = executor.submit(pending_slots.peek, session_id).result() or {}
        if item.get("intent") == "aa_collect":
            st.session_state["aa-create-open"] = True     # 正在补槽 → 直接展开
        #: 2026-10-30（用户：收起按钮点了没用，干脆去掉）：
        #: 只在**收起时**给一个「＋ 新增群收款」入口；展开后不再放收起按钮，
        #: 办完（发起/完成）会自动收起（`_start` / `_say` 里清状态）。
        open_now = bool(st.session_state.get("aa-create-open"))
        if not open_now:
            if st.button("＋ 新增群收款", key="aa-create-toggle", type="primary",
                         width="stretch"):
                _reset_form()                     # 新起一笔 → 金额/备注/参与人全部归零
                st.session_state["aa-create-open"] = True
                st.rerun()
        else:
            _create_panel(executor, session_id)
    #: 2026-10-31（用户要求）：正在**新增**群收款时，下面不再铺历史群收款状态卡 ——
    #: 这一屏只服务新起一笔，免得旧单子把视线拉走（发起/确认后历史会自己回来）。
    creating = bool(st.session_state.get("aa-create-open")) and confirmation is None
    if not creating:
        for item in executor.submit(aa_flow.active).result():
            _status_card(executor, session_id, item)
