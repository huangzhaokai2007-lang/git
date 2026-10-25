"""工具层 §5 风控/限额内核（任务卡 05b 从 `tools/transfer.py` 拆出）：权限档、降级因子、剩余限额。

依赖方向**单向**：`tools/transfer.py` → 本模块 → `tools/_query_common.py` / `tools/schemas.py`；
本模块**绝不** import `tools.transfer`（否则形成回边，卡 05b 验收门 ② 会 FAIL）。

**时间由参数注入**（`moment: datetime`）：`_now` 留在 `transfer.py`（测试用假时钟钉 `transfer._now`），
若这里也读 `_now` 就得反向 import → 回边。注入后本模块是纯函数，更好测。

⚠ `VELOCITY_MINUTES_WRITE`=10（§5 写操作降级窗口）与 `tools/_query_analysis.VELOCITY_MINUTES_T4`=60
（T4 只读高频检测）**同名不同义**，卡 05b 要求 #3：两者不可合并。
"""

from __future__ import annotations

import datetime

from datetime import datetime, timedelta
from data import dao
from tools._query_common import ToolError, _owned_account_ids
from tools.schemas import ErrorCode, MAX_LIMIT

# ---------------- §5 阈值常量 ----------------

L1_MAX_CENTS = 50_000               # 来源：规格 §5 L1「白名单收款人 且 金额 ≤ 50000 分（500元）」

SINGLE_TX_MAX_CENTS = 50_000        # 来源：规格 §5 硬约束「单笔上限 5 万分/笔」

DAILY_MAX_CENTS = 200_000           # 来源：规格 §5 硬约束「单日累计 20 万分」

NIGHT_START_HOUR, NIGHT_END_HOUR = 23, 6   # 来源：规格 §5「night(23:00-06:00)」

VELOCITY_MINUTES_WRITE = 10        # 来源：规格 §5「velocity(10分钟内≥3笔写操作)」

VELOCITY_MIN_WRITES = 3             # 来源：同上「≥3笔」

AMOUNT_JUMP_RATIO = 5               # 来源：规格 §5「amount_jump(>历史均值5倍)」——**写操作降级因子**，

HISTORY_DAYS = 90                   # 来源：历史均值窗口；§5 未定义窗口，沿用卡 04 已确立的「近 90 天」

TIER_ORDER: tuple[str, ...] = ("L0", "L1", "L2", "L3")

OTP_TIERS = ("L2", "L3")


def _today_out_flows(moment: datetime) -> list[dict]:
    """今日（`_now()` 当天）该用户的支出流水（供单日累计与短时高频两个口径复用）。"""
    today = moment.date().isoformat()
    try:
        page = dao.list_txn(today, today, limit=MAX_LIMIT)
    except ValueError as exc:                                    # 理论上不会发生（日期由 _now 生成）
        raise ToolError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
    owned = _owned_account_ids()
    return [row for row in page["items"] if row["amount"] < 0 and row["account_id"] in owned]

def _history_mean_cents(moment: datetime) -> tuple[int, int]:
    """近 HISTORY_DAYS 天支出均值的整数地板值 + 样本数（金额偏离因子用）。

    样本 = 单次翻页上限内**最近** MAX_LIMIT 笔（样本数进 facts 供审计）；无样本 → 0（该因子不判定）。
    """
    begin = (moment - timedelta(days=HISTORY_DAYS)).date().isoformat()
    try:
        page = dao.list_txn(begin, moment.date().isoformat(), limit=MAX_LIMIT)
    except ValueError as exc:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
    owned = _owned_account_ids()
    outs = [-row["amount"] for row in page["items"]
            if row["amount"] < 0 and row["account_id"] in owned and row["ts"] < moment.isoformat()]
    return (sum(outs) // len(outs) if outs else 0), len(outs)

def _factors(payee: dict, cents: int, flows: list[dict], moment: datetime) -> list[str]:
    """规格 §5 的降级因子（命中任一 → 升一档；≥2 → 转人工）。

    `device_change` / `geo_change` 无数据源（DDL 无设备/地理列），本卡不实现。
    """
    floor = (moment - timedelta(minutes=VELOCITY_MINUTES_WRITE)).isoformat()
    hits = []
    if moment.hour >= NIGHT_START_HOUR or moment.hour < NIGHT_END_HOUR:
        hits.append("night")
    if not payee["is_whitelist"] or payee["last_used_ts"] is None:
        hits.append("new_payee")
    recent = len([row for row in flows if floor <= row["ts"] <= moment.isoformat()])
    if recent + 1 >= VELOCITY_MIN_WRITES:            # 含本次这一笔
        hits.append("velocity")
    mean, _ = _history_mean_cents(moment)
    if mean > 0 and cents > mean * AMOUNT_JUMP_RATIO:
        hits.append("amount_jump")
    return hits

def _assess(payee: dict, cents: int, flows: list[dict], moment: datetime) -> dict:
    """按规格 §5 定档：基础档 + 降级因子升档。

    规格 §5 的 L2 基础条件本来就是「新收款人」，而 `new_payee` 又在降级因子清单里 —— 同一条
    不能既定基础档又再升一档（否则非白名单收款人永远落到 L3、与「L2 + OTP」的口径自相矛盾）。
    故：基础档已体现过的那条不再参与升档；`factors` 仍如实给出**全部命中**（透明），`escalation` 给升档数。
    """
    hits = _factors(payee, cents, flows, moment)
    whitelisted = bool(payee["is_whitelist"])
    base = "L1" if (whitelisted and cents <= L1_MAX_CENTS) else "L2"
    escalation = [hit for hit in hits if not (hit == "new_payee" and not whitelisted)]
    tier = TIER_ORDER[min(TIER_ORDER.index(base) + len(escalation), len(TIER_ORDER) - 1)]
    return {"tier": tier, "factors": hits, "escalation": len(escalation),
            "requires_otp": tier in OTP_TIERS, "to_human": tier == "L3"}

def _limits(flows: list[dict]) -> dict:
    """剩余限额：单笔上限 / 单日累计上限 / 今日已用 / 今日剩余（全部整数分）。"""
    used = sum(-row["amount"] for row in flows)
    return {"single_max": SINGLE_TX_MAX_CENTS, "daily_max": DAILY_MAX_CENTS,
            "daily_used": used, "daily_remaining": max(DAILY_MAX_CENTS - used, 0)}


# ---------------- 白名单专属额度（工具层唯一实现，2026-10-24） ----------------

def apply_whitelist_quota(payee: dict, amount: int, judged: dict) -> dict:
    """白名单 + 未过期 + 剩余额度够 + **只有夜间因子** → 免验证码（tier L1）。

    为什么必须在**工具层**做：预览 token 里的 `tier`/`requires_otp` 就是执行时（T8/T21）的闸门。
    编排层「把卡面改成 L1」是**不够**的 —— 那会让卡片显示免码、执行时却因 token 要求 OTP 而失败
    （实测：白名单额度内转账点确认 → 「验证码不正确」）。
    口径与 `agent/payee_risk.apply_quota` 一致：额度内只免「深夜」这一项，其它异常因子照常升档。
    """
    if not payee or not payee.get("is_whitelist") or payee.get("blacklist"):
        return judged
    expire = str(payee.get("whitelist_expire") or "")[:10]
    today = datetime.today().date()              # 本模块的 datetime 是类（下面 from datetime import datetime）
    if expire:
        try:
            if today > datetime.fromisoformat(expire).date():
                return judged                       # 过期 → 按普通处理（要码）
        except ValueError:
            pass
    quota = payee.get("whitelist_quota")
    if quota is None:
        return judged
    try:
        from data import payee_dao
        remaining = payee_dao.remaining_quota_cents(dict(payee), today.strftime("%Y-%m"))
    except Exception:                                # noqa: BLE001 —— 算不出就按原档位（保守）
        return judged
    if remaining is None or int(amount) > remaining:
        return judged
    others = [f for f in (judged.get("factors") or []) if f != "night"]
    if others:
        return judged                                # 有其它异常因子 → 照常升档
    patched = dict(judged)
    patched.update({"tier": "L1", "requires_otp": False,
                    "quota_note": "白名单专属额度内，转账免验证码（含深夜时段）"})
    return patched
