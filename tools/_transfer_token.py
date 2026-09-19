"""transfer 的 token 快照存储（卡 14b-6）：幂等从进程内存切到落库 `idempotency` 的薄壳。

拆出原因：`tools/transfer.py` 逼近 CLAUDE.md「单文件 ≤300 行」上限，把 token 落库相关的
内存缓存 `_TOKENS` / 临界区锁 `_TOKENS_LOCK` / 读薄壳 `_load_token` / 写薄壳 `_store_token`
收进本模块，与既有 `_transfer_risk.py` / `_query_common.py` 的拆分方式一致（卡 05b）。

依赖单向 `_transfer_token → data.dao`，无回边。`_TOKENS` 由 `transfer.py` re-export 给测试
（`conftest` 逐用例复位 `transfer._TOKENS.clear()`）；**落库 `idempotency` 是唯一真相**，
进程重启后重放同 token 仍从库文件取回同一份结果。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from data import dao

#: token 快照内存缓存（同进程快路径）；真相在 `idempotency` 落库表。
_TOKENS: dict[str, dict] = {}
#: 幂等临界区（卡 04b）：token 状态检查 → 翻转 → 扣款事务必须整体串行。
#: 旧实现把 `state == "executed"` 检查放在事务外，两线程可同时通过 → 双重扣款（线程级 TOCTOU）。
#: 另注：DAO 是进程内**单个** SQLite 连接，并发写本来也必须串行，一把锁同时解决这两个问题。
_TOKENS_LOCK = threading.Lock()


def _serialize_datetime(value: object) -> str:
    """`json.dumps` 的 `default`：把 token 快照里的 `created_at`(datetime) 转 ISO 字符串。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    raise TypeError(f"token 快照含不可序列化类型：{type(value)!r}")


def _dumps(payload: dict) -> str:
    """token 快照 → 确定性 JSON 字符串（sort_keys 保证 CAS 比对稳定）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_serialize_datetime)


def _load_token(token: str) -> dict | None:
    """薄壳：读 token 快照（同进程优先内存快路径，内存无则落库）。

    ⚠ 卡 14b-6 MUST_FIX：**state 判定一律用 `_load_token_authoritative`**（见下），
    本函数保留给"确定不需要 state 权威性"的场景；缓存可能是 stale 的，不能用来判定 executed。
    """
    cached = _TOKENS.get(token)
    if cached is not None:
        return cached
    return _load_token_authoritative(token)


def _load_token_authoritative(token: str) -> dict | None:
    """**权威读**：绕过内存缓存，直接从 `idempotency` 落库取，并刷新缓存。

    为什么必须有它：进程内缓存只反映本进程见过的状态，库里可能已经被别人（另一进程/上一个请求）
    翻成 `executed` —— 用缓存判 `state` 会得出错误结论、进而**二次扣款**。state 判定只认库。
    """
    row = dao.get_idempotent(token)
    if row is None:
        _TOKENS.pop(token, None)                                 # 库里没有 → 清掉脏缓存
        return None
    payload = json.loads(row["result_json"])
    payload["created_at"] = datetime.fromisoformat(payload["created_at"])
    _TOKENS[token] = payload                                     # 顺手把缓存刷新成权威值
    return payload


def _raw_snapshot(token: str) -> str | None:
    """取库里那份 JSON 原文（**绕过缓存**）—— 用作 CAS 的 expected 值，必须来自库而非内存。"""
    row = dao.get_idempotent(token)
    return None if row is None else row["result_json"]


def _store_token(token: str, payload: dict, *, expect_json: str | None = None) -> bool:
    """薄壳：写 token 快照（token 是主键，整个 dict 序列化进 `result_json`）。

    - `expect_json is None`（preview 首次登记）：无行则 INSERT；
    - 否则做 **CAS**：仅当库里仍是 `expect_json`（即调用方**加载时捕获**的那份）才覆盖。
      ⚠ 卡 14b-6 MUST_FIX：expected 必须是调用方捕获的旧值，**不能**现读库再比 —— 那样永远命中，守卫是假的。
    返回是否翻转成功；`False` = 已被抢先执行，调用方应从库取**赢家**快照走幂等返回。
    """
    new_json = _dumps(payload)
    created_at = payload["created_at"].isoformat(timespec="seconds")
    if expect_json is None:
        if dao.get_idempotent(token) is None:
            dao.insert_idempotent(token, "transfer", payload["user_id"], new_json, created_at)
        else:
            dao.update_idempotent(token, new_json, expect_json)   # 已存在则当作"同一份"覆盖（preview 重复调用）
        _TOKENS[token] = payload
        return True
    flipped = dao.update_idempotent(token, new_json, expect_json) > 0
    if flipped:
        _TOKENS[token] = payload
    else:
        _load_token_authoritative(token)                          # 输给赢家：把缓存刷成库里的权威值
    return flipped
