"""收款人解析关卡（转账域 v2 的**唯一实现**）。

**存在的理由**：转账、定时转账、AA 收款、送礼计划四个出口都需要「把用户说的那个人变成收款人 id」。
此前它们各写一遍（`_shape_transfer` / `_shape_scheduled` / `_shape_aa` / `_shape_gift`），
于是「同名的两个李四」在四个功能里可能给出**不同**的结论 —— 这就是「功能之间没关联」的具体来源。

本模块把这一步收成一条路径，四个出口都调它：

```
标签精确命中 → 姓名精确 → 拼音全等（同音） → 回落 T6 子串匹配
                    ↓
        唯一 → unique        多个 → ambiguous（出解析卡）
        零个 → none          命中黑名单 → blacklisted（直接拒，不当候选）
```

**分层**：本模块属编排层，只调 `tools/`（T6 `resolve_payee` + T20 `list_payees`），
不碰 SQL、不算金额。标签/黑名单来自 T20 的 `facts["profiles"]`（`data` 是冻结契约，不能加键）。

**只在会话内记「解析结果」**：用户刚选过谁、刚加过谁，后续直接命中不重复追问 ——
与 `agent/session_memory.py` 同寿命（进程内、无持久化，丢失只影响追问、不涉及资金）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from pypinyin import lazy_pinyin

from tools import payee, transfer

logger = logging.getLogger(__name__)

#: 解析结论。`blacklisted` 与其它三者并列：它不是「没找到」，而是「明确禁止」。
KIND_UNIQUE, KIND_AMBIGUOUS, KIND_NONE, KIND_BLACKLISTED = (
    "unique", "ambiguous", "none", "blacklisted")

#: 命中方式（写进 facts 与日志，便于排查「为什么匹配到了这个人」）
HOW_TAG, HOW_EXACT, HOW_PINYIN, HOW_SUBSTRING, HOW_MEMORY = (
    "tag", "exact", "pinyin", "substring", "memory")

#: 会话 → 上一次确定选中的收款人 id（刚选中/刚添加的人，后续直接命中）
_PICKED: dict[str, str] = {}


@dataclass(frozen=True)
class ResolveResult:
    """一次解析的结论。`payees` 是候选（含黑名单标记），`clarify` 是给用户的追问/说明。"""

    kind: str
    payees: tuple[Mapping, ...] = ()
    how: str = ""
    clarify: str = ""
    #: 命中黑名单时的那一位（用于回执里说清「谁被拉了黑名单」）
    blocked: Mapping | None = None

    @property
    def ok_to_proceed(self) -> bool:
        return self.kind == KIND_UNIQUE


def remember(session_id: str, payee_id: str) -> None:
    """记住「这个会话刚刚确定了这个人」—— 下一句同类请求直接命中。"""
    if session_id and payee_id:
        _PICKED[session_id] = payee_id


def reset(session_id: str | None = None) -> None:
    """清空解析记忆（测试隔离与界面「重置会话态」用）。"""
    if session_id is None:
        _PICKED.clear()
    else:
        _PICKED.pop(session_id, None)


def _py(text: str) -> tuple[str, ...]:
    """拼音（不含声调）—— 同音判定用。非中文/空 → 空元组（不参与同音比较）。"""
    raw = str(text or "").strip()
    return tuple(lazy_pinyin(raw)) if raw else ()


def _people() -> list[dict]:
    """当前用户的收款人 + 标签/黑名单画像（来自 T20；工具失败则空列表，由调用方报错）。"""
    listed = payee.list_payees()
    if not listed.ok:
        logger.warning("解析关卡：取收款人列表失败：%s", listed.message)
        return []
    profiles = listed.facts.get("profiles") or {}
    out = []
    for item in listed.facts.get("items") or []:
        extra = profiles.get(item["payee_id"]) or {}
        out.append({**item, "tags": extra.get("tags") or [],
                    "blacklist": bool(extra.get("blacklist")),
                    "blacklist_reason": extra.get("blacklist_reason"),
                    "whitelist_quota": extra.get("whitelist_quota")})
    return out


def _match(query: str, people: list[dict]) -> tuple[list[dict], str]:
    """按「标签 → 姓名精确 → 拼音全等 → 子串」的顺序找；**先命中的层级独享结果**。"""
    if not query:
        return [], ""
    tagged = [p for p in people if any(query == t or query in t for t in p["tags"])]
    if tagged:
        return tagged, HOW_TAG
    exact = [p for p in people if p["name"] == query]
    if exact:
        return exact, HOW_EXACT
    want = _py(query)
    if want:
        homophone = [p for p in people if _py(p["name"]) == want]
        if homophone:
            return homophone, HOW_PINYIN
    found = transfer.resolve_payee(query)             # 回落 T6：姓名/手机号/银行的子串匹配
    if not found.ok:
        return [], ""
    ids = {c["id"] for c in (found.facts.get("candidates") or [])}
    return [p for p in people if p["payee_id"] in ids], HOW_SUBSTRING


def resolve(text: str, *, session_id: str = "") -> ResolveResult:
    """把「用户说的那个人」解析成收款人。**四个出口共用这一个函数。**

    `session_id` 非空时先看会话记忆：上一句刚确定过谁，这一句同样的称呼直接命中他
    （用户不必重复"1001"）。
    """
    query = str(text or "").strip()
    people = _people()
    if not people:
        return ResolveResult(kind=KIND_NONE, clarify="您还没有收款人。可以说「我要加个收款人」来添加。")

    picked_id = _PICKED.get(session_id) if session_id else None
    if picked_id:
        hit = [p for p in people if p["payee_id"] == picked_id]
        if hit and (hit[0]["name"] == query or query in hit[0]["tags"]):
            return _decide(hit, HOW_MEMORY, query)

    hits, how = _match(query, people)
    return _decide(hits, how, query)


def resolve_or_explain(text: str, *, session_id: str = "") -> tuple[dict | None, str | None, dict]:
    """**四个出口共用的返回形状**：`(唯一命中的人, 错误码, 候选事实包)`。

    - 唯一 → `(person, None, {})`，并把这次命中记进会话记忆；
    - 黑名单 → `(None, "FORBIDDEN", {"message": 说明})` —— 调用方**直接出错误回执**，不出确认卡；
    - 多个/零个 → `(None, None, {"candidates": [...]})` —— 调用方把它交给
      `payee_clarify.ask_for(facts, missing)`，**连同自己缺的其它槽位一起追问**。

    ⚠️ **为什么返回 facts 而不是一句文案**（S4 第一版就栽在这）：追问要**合起来问** ——
    「找到 2 个李四，请确认是哪一个；另外金额还没说」。若关卡自己生成文案，
    调用方「缺金额」那半句就丢了（实测退化成只问人）。候选由本模块给、措辞由
    `payee_clarify` 统一生成，两边都不越界。
    """
    got = resolve(text, session_id=session_id)
    if got.kind == KIND_UNIQUE:
        person = dict(got.payees[0])
        remember(session_id, str(person["payee_id"]))
        return person, None, {}
    if got.kind == KIND_BLACKLISTED:
        return None, "FORBIDDEN", {"message": got.clarify}
    return None, None, {"candidates": [{"id": p["payee_id"], "name": p["name"],
                                        "phone": p["masked_phone"]} for p in got.payees]}


def profile_of(payee_id: str) -> dict | None:
    """按 id 取一个人的完整画像（tags / blacklist / whitelist_quota）—— 供风控关卡用。

    `_shape_*` 只把 `payee_id` 塞进 `filled`，`_prepare_*` 要判「白名单额度够不够」时，
    用本函数拿回画像；读的是 T20 的 `facts.profiles`，不直查库。
    """
    return next((p for p in _people() if p["payee_id"] == payee_id), None)


def _decide(hits: list[dict], how: str, query: str) -> ResolveResult:
    """按命中结果给结论：黑名单优先于一切（命中黑名单不当候选，直接拒）。"""
    if not hits:
        return ResolveResult(kind=KIND_NONE, how=HOW_SUBSTRING,
                             clarify=f"没有找到「{query}」。可以说「我要加个收款人」来添加。")
    blocked = [p for p in hits if p["blacklist"]]
    if blocked and len(blocked) == len(hits):
        only = blocked[0]
        reason = only.get("blacklist_reason") or ""
        text = f"{only['name']}（{only['masked_phone']}）已在黑名单，不能转账或送礼。"
        if reason:
            text += f"原因：{reason}。"
        return ResolveResult(kind=KIND_BLACKLISTED, payees=tuple(hits), how=how,
                             clarify=text, blocked=only)
    live = [p for p in hits if not p["blacklist"]]
    if len(live) == 1:
        return ResolveResult(kind=KIND_UNIQUE, payees=tuple(live), how=how)
    names = "、".join(f"{p['name']}（{p['masked_phone']}）" for p in live)
    return ResolveResult(kind=KIND_AMBIGUOUS, payees=tuple(live), how=how,
                         clarify=f"找到 {len(live)} 个可能的人，请确认是哪一个：{names}。")
