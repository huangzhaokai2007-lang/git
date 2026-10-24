"""查看白名单（T20 `payee_list`）的**卡片**渲染 —— 只在网页端美化。

CLI / API / IM 拿到的仍是同一份**文本回执**（`agent/render_lists.render_payees` 的 Markdown 表格），
本模块只在 Streamlit 里把那张表画成卡片，不改 agent/ 的任何一行。

**为什么解析回执、不重新取数**（分层 + 数字纪律）：
- `interfaces/` 只能调 `agent/`，**不许**直接调 `tools/`；白名单卡片是 T20 时写的，那时
  `orchestrator.Turn` 还不携带 facts（卡 25 起只读路径带了 —— 新写的图表卡片走 facts，
  见 `insight_card_ui`，本卡仍按老口径解析回执，两种都合法，先例不强制回改）；
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
.payee-card{border:1px solid #2c2d33;border-radius:14px;background:#151618;margin:2px 0 4px;
 padding:12px 16px 10px;box-shadow:0 1px 3px rgba(16,24,40,.08);color:#d1d4dc;font-size:14px}
.payee-card-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
 padding-bottom:9px;border-bottom:1px dashed #2c2d33}
.payee-card-title{font-weight:650;font-size:15px}
.payee-card-sub{color:#a1a5af;font-size:12.5px}
.payee-row{display:flex;align-items:center;gap:10px;padding:7px 0}
.payee-row+.payee-row{border-top:1px solid #2c2d33}
.payee-avatar{width:26px;height:26px;flex:0 0 26px;border-radius:50%;background:#2b2940;color:#b7b1ff;
 display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
.payee-name{min-width:66px;font-weight:600}
.payee-phone{font-family:ui-monospace,Consolas,monospace;color:#d1d4dc;letter-spacing:.3px}
.payee-bank{color:#a1a5af;flex:1 1 auto;min-width:60px}
.payee-badge{font-size:11.5px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.payee-badge.on{background:#183026;color:#9bd3b0;border:1px solid #315843}
.payee-badge.off{background:#23252b;color:#b8bdc8;border:1px solid #3a3d46}
.payee-card-foot{color:#a1a5af;font-size:12px;line-height:1.55;margin-top:6px;
 padding-top:8px;border-top:1px solid #2c2d33}
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
#: 上方汤版只渲染回执表格；下面这组给每行加「转账 / 加白 / 拉黑」按钮，
#: 按钮 = 替用户说一句完整的话、走同一条 orchestrator.handle 流水线。

import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from agent import orchestrator, payee_resolve

#: 三类人员的中文标签（面向用户只说大白话；与 `render_lists` 的措辞同口径）
KIND_CN = {"whitelist": "白名单", "blacklist": "黑名单", "normal": "普通"}


def _dispatch(executor: ThreadPoolExecutor, session_id: str, display: str, text: str) -> None:
    """替用户发一句话，并把回执记进会话（与 `app._ask` 同口径，不写业务）。"""
    state = st.session_state
    state["messages"].append({"role": "user", "text": display})
    turn = executor.submit(orchestrator.handle, text, session_id=session_id).result()
    state["messages"].append({"role": "assistant", "text": turn.reply, "turn": turn.model_dump()})
    state["turns"].append({"ts": time.strftime("%H:%M:%S"), "text": display, "turn": turn.model_dump()})
    state["clarify_round"] = state.get("clarify_round", 0) + (1 if "CLARIFY" in turn.states else 0)


def render(executor: ThreadPoolExecutor, session_id: str) -> None:
    """渲染收款人列表卡；每行给「转账 / 加白 / 拉黑或解除 / 删除」按钮。

    列表来自 `agent.payee_resolve.list_people()`（只读当前 user；黑白名单、标签一并带回）。
    """
    people = executor.submit(payee_resolve.list_people).result()
    if not people:
        return
    st.markdown("##### 👥 收款人")
    for person in people:
        kind = ("whitelist" if person["is_whitelist"]
                else "blacklist" if person["blacklist"] else "normal")
        tags = f" · 标签 {', '.join(person['tags'])}" if person.get("tags") else ""
        left, transfer_btn, whitelist_btn, blacklist_btn, delete_btn = st.columns(
            [4, 1, 1, 1, 1], vertical_alignment="center")
        left.markdown(f"**{person['name']}** · `{person['masked_phone']}`"
                      f" · {KIND_CN[kind]}{tags}")
        if transfer_btn.button("转账", key=f"tr-{person['payee_id']}",
                               help=f"给 {person['name']} 转账"):
            _dispatch(executor, session_id, f"（按钮）给{person['name']}转账",
                      f"给{person['name']}转账")
            st.rerun()
        if not person["is_whitelist"] and not person["blacklist"]:
            if whitelist_btn.button("加白", key=f"wl-{person['payee_id']}",
                                    help=f"把 {person['name']} 加入白名单（需验证码）"):
                _dispatch(executor, session_id, f"（按钮）把{person['name']}加入白名单",
                          f"把{person['name']}加入白名单")
                st.rerun()
        if not person["blacklist"]:
            if blacklist_btn.button("拉黑", key=f"bl-{person['payee_id']}",
                                    help=f"把 {person['name']} 加入黑名单（之后转账/送礼被拒）"):
                _dispatch(executor, session_id, f"（按钮）把{person['name']}加入黑名单",
                          f"把{person['name']}加入黑名单")
                st.rerun()
        else:
            if blacklist_btn.button("解除", key=f"ub-{person['payee_id']}",
                                    help=f"把 {person['name']} 移出黑名单（需验证码）"):
                _dispatch(executor, session_id, f"（按钮）把{person['name']}移出黑名单",
                          f"把{person['name']}移出黑名单")
                st.rerun()
        if delete_btn.button("删除", key=f"del-{person['payee_id']}",
                             help=f"删除收款人 {person['name']}（需确认）"):
            _dispatch(executor, session_id, f"（按钮）删除收款人{person['name']}",
                      f"删除收款人{person['name']}")
            st.rerun()
