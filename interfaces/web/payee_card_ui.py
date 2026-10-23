"""查看白名单（T20 `payee_list`）的**卡片**渲染 —— 只在网页端美化。

CLI / API / IM 拿到的仍是同一份**文本回执**（`agent/render_lists.render_payees` 的 Markdown 表格），
本模块只在 Streamlit 里把那张表画成卡片，不改 agent/ 的任何一行。

**为什么解析回执、不重新取数**（分层 + 数字纪律）：
- `interfaces/` 只能调 `agent/`，**不许**直接调 `tools/`；`orchestrator.Turn` 也不携带 facts；
- 仓内先例：`components.parse_bill` 同样是「编排层回执文本 → 绘图坐标」的解析（见 components 的
  docstring 第 6–7 行）；
- 卡片里的每个字都**从回执里照抄**（姓名 / 脱敏手机号 / 银行 / 是否白名单），不重算、不补数（铁律 2）。

**失败即降级**：`render_payee_card` 解不出表格 → 返回 `False`，调用方回退到纯文本回执。
宁可朴素，也不许空白或崩 —— 与 `components.pending_card` 的「认不出就当没有」同口径。
"""

from __future__ import annotations

import html
import re
from typing import NamedTuple

import streamlit as st

#: 表头必须逐字等于回执里的这四列（`agent/render_lists.T_PAYEE_TABLE_HEAD`）。
#: 用它当**形状校验**：列名一改就自动降级成纯文本，而不是把错位的格子画进卡片。
_EXPECTED_COLUMNS = ("收款人", "手机号", "银行", "白名单")

#: Markdown 表格的分隔行，如 `| --- | --- | --- | --- |`
_SEP_LINE = re.compile(r"^\|[\s:|-]+\|$")

#: 单元格里的「是 / 否」两种标记（同样来自回执：`T_PAYEE_YES` / `T_PAYEE_NO`）
_MARK_YES = "✅"


class PayeeCard(NamedTuple):
    """一张卡片的三个部分：汇总句、明细行、表下说明（全部原样取自回执）。

    用 `NamedTuple` 而不是 `@dataclass`：本模块会被测试**按文件路径**加载
    （`importlib.util.spec_from_file_location`，仓内 `test_web_layering` 的做法），
    那种加载方式下 `dataclass` 找不到 `sys.modules[cls.__module__]` 会直接抛
    `AttributeError`。`NamedTuple` 没有这个约束。
    """

    head: str
    rows: tuple[tuple[str, str, str, str], ...]      # 姓名, 脱敏手机号, 银行, 白名单标记
    foot: str


def _cells(line: str) -> list[str]:
    """把 `| a | b | c | d |` 切成 `['a', 'b', 'c', 'd']`（只切表格语法，不改内容）。"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_payee_reply(reply: str) -> PayeeCard | None:
    """回执 → `PayeeCard`；**不是**这张表（列名不符 / 没有明细行 / 分隔行缺失）→ `None`。"""
    lines = [line.strip() for line in str(reply).splitlines()]
    start = next((index for index, line in enumerate(lines) if line.startswith("|")), None)
    if start is None or start + 1 >= len(lines):
        return None
    if tuple(_cells(lines[start])) != _EXPECTED_COLUMNS or not _SEP_LINE.match(lines[start + 1]):
        return None
    rows: list[tuple[str, str, str, str]] = []
    index = start + 2
    while index < len(lines) and lines[index].startswith("|"):
        cells = _cells(lines[index])
        if len(cells) != len(_EXPECTED_COLUMNS):
            return None
        rows.append((cells[0], cells[1], cells[2], cells[3]))
        index += 1
    if not rows:
        return None
    head = next((line for line in lines[:start] if line), "")
    foot = next((line for line in lines[index:] if line), "")
    return PayeeCard(head=head, rows=tuple(rows), foot=foot)


#: 卡片样式：圆角 + 细边框 + 轻阴影；白名单用绿色徽章，非白名单用灰色（两组的验证方式不同）。
_CSS = """<style>
.payee-card{border:1px solid #e3e8ef;border-radius:14px;background:#fff;margin:2px 0 4px;
 padding:12px 16px 10px;box-shadow:0 1px 3px rgba(16,24,40,.08);color:#1f2937;font-size:14px}
.payee-card-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
 padding-bottom:9px;border-bottom:1px dashed #e3e8ef}
.payee-card-title{font-weight:650;font-size:15px}
.payee-card-sub{color:#6b7280;font-size:12.5px}
.payee-row{display:flex;align-items:center;gap:10px;padding:7px 0}
.payee-row+.payee-row{border-top:1px solid #f3f4f6}
.payee-avatar{width:26px;height:26px;flex:0 0 26px;border-radius:50%;background:#eef2ff;color:#4338ca;
 display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
.payee-name{min-width:66px;font-weight:600}
.payee-phone{font-family:ui-monospace,Consolas,monospace;color:#374151;letter-spacing:.3px}
.payee-bank{color:#6b7280;flex:1 1 auto;min-width:60px}
.payee-badge{font-size:11.5px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.payee-badge.on{background:#ecfdf3;color:#067647;border:1px solid #abefc6}
.payee-badge.off{background:#f2f4f7;color:#475467;border:1px solid #e4e7ec}
.payee-card-foot{color:#6b7280;font-size:12px;line-height:1.55;margin-top:6px;
 padding-top:8px;border-top:1px solid #f3f4f6}
</style>"""


def _row_html(row: tuple[str, str, str, str]) -> str:
    """一行明细（四个格子全部 `html.escape`：姓名与银行名来自库，按不可信文本处理）。"""
    name, phone, bank, mark = (html.escape(value) for value in row)
    is_white = mark.strip() == _MARK_YES
    badge = ('<span class="payee-badge on">白名单</span>' if is_white
             else '<span class="payee-badge off">非白名单</span>')
    avatar = html.escape(row[0][:1])                 # 首字：只做头像，不是业务数据
    return (f'<div class="payee-row">'
            f'<span class="payee-avatar">{avatar}</span>'
            f'<span class="payee-name">{name}</span>'
            f'<span class="payee-phone">{phone}</span>'
            f'<span class="payee-bank">{bank}</span>{badge}</div>')


def _card_html(card: PayeeCard) -> str:
    return (f'<div class="payee-card">'
            f'<div class="payee-card-head"><span class="payee-card-title">👥 我的收款人</span>'
            f'<span class="payee-card-sub">{html.escape(card.head)}</span></div>'
            + "".join(_row_html(row) for row in card.rows)
            + (f'<div class="payee-card-foot">{html.escape(card.foot)}</div>'
               if card.foot else "")
            + '</div>')


def render_payee_card(reply: str) -> bool:
    """把回执画成卡片；返回**是否画了**（`False` = 调用方回退到纯文本）。"""
    card = parse_payee_reply(reply)
    if card is None:
        return False
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_card_html(card), unsafe_allow_html=True)
    return True


# ================= S6：行内操作按钮（与上方「只读卡片」并存，不互斥） =================
#: 上方汤版只渲染回执表格；下面这组给每行加「转账 / 加白 / 拉黑 / 删除」按钮。
#: 按钮**确定性直达写流水线**（`write_flow.start`，意图是按钮给的、不是模型猜的）——
#: 实测缺陷：走 orchestrator.handle 时，若上一张确认卡还在途，按钮消息会被当成
#: 「在途卡回复」串台（拉黑按钮弹出转账确认卡）。

import time
from concurrent.futures import ThreadPoolExecutor

import datetime
import streamlit as st

from agent import confirm_card, payee_resolve, payee_risk, write_flow

#: 三类人员的中文标签（面向用户只说大白话；与 `render_lists` 的措辞同口径）
KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}

#: 白名单有效期的展示状态阈值（**仅展示**，不参与风控 —— 人类明确口径）
_EXPIRE_SOON_DAYS = 30


def _expire_note(person: dict) -> str:
    """白名单有效期的展示串：有效 / 即将到期 / 已过期（纯展示，不参与免码判定）。"""
    expire = str(person.get("whitelist_expire") or "")
    if not expire or not person.get("is_whitelist"):
        return ""
    try:
        deadline = datetime.date.fromisoformat(expire[:10])
    except ValueError:
        return ""
    left = (deadline - datetime.date.today()).days
    if left < 0:
        return f" · 白名单已过期（{expire}）"
    if left <= _EXPIRE_SOON_DAYS:
        return f" · 白名单至 {expire}（{left} 天内到期）"
    return f" · 白名单至 {expire}"


def _dispatch_start(executor: ThreadPoolExecutor, session_id: str, intent: str,
                    slots: dict, display: str) -> None:
    """按钮直达 `write_flow.start`（零 LLM 分类），并把回执记进会话。"""
    state = st.session_state
    state["messages"].append({"role": "user", "text": display})
    confirm_card.clear(session_id)                       # 在途卡作废：按钮是新一笔操作
    step = executor.submit(write_flow.start, intent, slots, session_id).result()
    text = step.reply or step.ask or ""
    state["messages"].append({"role": "assistant", "text": text, "turn": None})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display, "turn": None})
    state["clarify_round"] = state.get("clarify_round", 0) + (1 if step.missing else 0)


#: 2026-10-24（人类规格「查询卡支持全部/白名单/黑名单/已过期白名单筛选」）
_FILTERS = ("全部", "白名单", "黑名单", "已过期白名单")


def _is_expired(person: dict) -> bool:
    """白名单是否已过期 —— 口径**只有一处**（`agent.payee_risk.is_expired`），界面不重写。"""
    return bool(person.get("is_whitelist")) and payee_risk.is_expired(person)


def _quota_note(executor: ThreadPoolExecutor, person: dict) -> str:
    """白名单专属额度：总额 + **本月剩余**（剩余额度由 `agent.payee_risk` 算，界面不重算）。"""
    total = person.get("whitelist_quota")
    if not person.get("is_whitelist") or total is None:
        return ""
    left = executor.submit(payee_risk.remaining_quota, person).result()
    left_txt = "—" if left is None else f"{left / 100:.2f} 元"
    return f" · 额度 {total / 100:.2f} 元（剩余 {left_txt}）"


def _recent_note(person: dict) -> str:
    """最近一次转账时间（工具层回填的 `last_used_ts`；没有就不印）。"""
    ts = str(person.get("last_used_ts") or "")
    return f" · 最近转账 {ts[:10]}" if ts else " · 从未转账"


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染收款人查询卡：筛选 + 统计 + 每行备注 + 「转账/加白/移出白名单/拉黑/解除/删除」+ 改备注表单。

    列表来自 `agent.payee_resolve.list_people()`（只读当前 user；黑白名单、标签、备注、有效期一并带回）。
    """
    all_people = executor.submit(payee_resolve.list_people).result()
    if not all_people:
        return
    st.markdown("##### 👥 收款人")
    st.caption(f"共 {len(all_people)} 人 · 白名单 "
               f"{sum(1 for p in all_people if p['is_whitelist'])} 人 · 黑名单 "
               f"{sum(1 for p in all_people if p['blacklist'])} 人 · 已过期白名单 "
               f"{sum(1 for p in all_people if _is_expired(p))} 人")
    picked = st.radio("筛选", _FILTERS, horizontal=True, key="payee-filter",
                      label_visibility="collapsed")
    people = [p for p in all_people
              if picked == "全部"
              or (picked == "白名单" and p["is_whitelist"] and not _is_expired(p))
              or (picked == "黑名单" and p["blacklist"])
              or (picked == "已过期白名单" and _is_expired(p))]
    if not people:
        st.caption("这一筛选下没有收款人。")
        return
    for person in people:
        kind = ("whitelist" if person["is_whitelist"]
                else "blacklist" if person["blacklist"] else "normal")
        tags = f" 标签 {', '.join(person['tags'])}" if person.get("tags") else ""
        #: 按钮**带 payee_id 直达**：同名收款人（种子里有两个李四）不该再触发解析追问 ——
        #: 用户在看到卡片时已经知道是谁了（按键就是在这个人那一行）
        who = {"payee_id": person["payee_id"], "payee": person["name"],
               "masked_phone": person["masked_phone"]}
        left, transfer_btn, whitelist_btn, blacklist_btn, delete_btn = st.columns(
            [3.4, 0.75, 0.75, 0.75, 0.75], vertical_alignment="center")
        note = f" · 备注 {person['note']}" if person.get("note") else ""
        quota = _quota_note(executor, person)
        recent = _recent_note(person)
        card = f" · 卡号 {person['card_no']}" if person.get("card_no") else ""
        left.markdown(f"**{person['name']}** `{person['masked_phone']}` · {KIND_CN[kind]}"
                      f"{tags}{_expire_note(person)}{quota}{note}{card}{recent}")
        if transfer_btn.button("转账", key=f"tr-{person['payee_id']}", width="content",
                               help=f"给 {person['name']} 转账"):
            _dispatch_start(executor, session_id, "transfer_single", who,
                            f"（按钮）给{person['name']}转账")
            st.rerun()
        if not person["is_whitelist"] and not person["blacklist"]:
            if whitelist_btn.button("加白", key=f"wl-{person['payee_id']}", width="content",
                                    help=f"把 {person['name']} 加入白名单（需验证码）"):
                _dispatch_start(executor, session_id, "payee_whitelist", who,
                                f"（按钮）把{person['name']}加入白名单")
                st.rerun()
        elif person["is_whitelist"]:
            if whitelist_btn.button("移出白名单", key=f"wlrm-{person['payee_id']}", width="content",
                                    help=f"把 {person['name']} 移出白名单（之后转账需要验证码）"):
                _dispatch_start(executor, session_id, "payee_whitelist_remove", who,
                                f"（按钮）把{person['name']}移出白名单")
                st.rerun()
        if kind == "normal":                              # 只有**普通**收款人能拉黑（白名单先移出）
            if blacklist_btn.button("拉黑", key=f"bl-{person['payee_id']}", width="content",
                                    help=f"把 {person['name']} 加入黑名单（之后转账/送礼被拒）"):
                _dispatch_start(executor, session_id, "payee_blacklist", who,
                                f"（按钮）把{person['name']}加入黑名单")
                st.rerun()
        elif person["blacklist"]:
            if blacklist_btn.button("解除", key=f"ub-{person['payee_id']}", width="content",
                                    help=f"把 {person['name']} 移出黑名单（需验证码）"):
                _dispatch_start(executor, session_id, "payee_unblacklist", who,
                                f"（按钮）把{person['name']}移出黑名单")
                st.rerun()
        if delete_btn.button("删除", key=f"del-{person['payee_id']}", width="content",
                             help=f"删除收款人 {person['name']}（需确认）"):
            _dispatch_start(executor, session_id, "payee_delete", who,
                            f"（按钮）删除收款人{person['name']}")
            st.rerun()
    with st.container(border=True):                        # 2026-10-24：查询卡上直接改备注
        st.markdown("###### ✏️ 改某位收款人的备注")
        names = {f"{p['name']}（{p['masked_phone']}）": p["payee_id"] for p in all_people}
        target = st.selectbox("改谁的备注", list(names), key="payee-note-pick")
        text = st.text_input("备注", key="payee-note-text", value="",
                             placeholder="例如：老同学 / 别转错（留空 = 清除）")
        if st.button("保存备注", key="payee-note-ok", type="primary"):
            _dispatch_start(executor, session_id, "payee_note",
                            {"payee_id": names[target], "note": text.strip()},
                            f"（按钮）改备注：{target}")
            st.rerun()
