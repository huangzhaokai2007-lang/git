"""护栏层：权限分级与降级（规格 §5）—— **纯代码判定，LLM 无权干预**（铁律 1）。

本模块只做一件事：把「写意图 (+ 金额/档位/降级因子)」判成一个 `TierVerdict`，供编排层在 PRECHECK
与 CONFIRM_CARD 之间调用。三个来源，逐条可溯：

1. **转账类**（`transfer_*`）：档位与降级因子在本仓已有唯一实现 —— `tools/_transfer_risk._assess`
   （card-05 已 PASS：含 §5 的 5 个降级因子、`night(23:00-06:00)`、`velocity(10min≥3笔)`、
   `amount_jump(>近90天均值5倍)`、以及「`new_payee` 已是 L2 基础条件、不重复升档」的口径）。
   本模块**复用**它（`preview_transfer` 的事实包就是它的输出），**绝不另写一份阈值表** ——
   两份实现漂移是审核师在 card-06/07 反复盯过的真风险。
2. **§5 表里直接点名的写意图**：`调额 / 取消代扣 / 申购赎回` → L2；`挂失 / 解锁` → L3（逐字来自 §5 表）。
3. **未登记的写意图**：**fail-closed 取 L3 + 转人工**（宁可多要一次人工复核，也不放低档跑）。

§5 的两条附加口径也在这里落地：
- 降级因子命中 **≥2 → 转人工**（`MIN_FACTORS_FOR_HUMAN`）；落到 L3 同样转人工（L3 的验证本来就含人工复核）。
- L3 = **延迟 60s 生效 + 可撤销**（`L3_DELAY_SECONDS`）；窗口状态由编排层持有（`agent/confirm_card.py`）。
"""

from __future__ import annotations

import logging
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from tools._transfer_risk import TIER_ORDER, _assess as _assess_transfer

logger = logging.getLogger(__name__)

READ_TIER = "L0"                      # 来源：规格 §5 表「L0 = 只读查询」

MIN_FACTORS_FOR_HUMAN = 2             # 来源：规格 §5「命中 ≥2 → 转人工」

L3_DELAY_SECONDS = 60                 # 来源：规格 §5 L3「延迟 60s 生效（可撤销）」

#: §5 表里逐字点名的写意图 → 基础档（未登记的一律 fail-closed 取 L3，见 `assess_write`）
INTENT_BASE_TIERS: dict[str, str] = {
    "card_adjust_limit": "L2",        # §5 L2「调额」
    "card_set_txn_limit": "L2",       # §5 L2「调额」
    "subscription_cancel": "L2",      # §5 L2「取消代扣」
    "wealth_buy": "L2",               # §5 L2「申购赎回」
    "wealth_redeem": "L2",            # §5 L2「申购赎回」
    "card_report_lost": "L3",         # §5 L3「挂失」
    "card_unlock": "L3",              # §5 L3「解锁」
}

#: 本模块能判定的转账类意图（档位来自工具层预览事实包）
TRANSFER_INTENTS = ("transfer_single", "transfer_scheduled")


class TierVerdict(BaseModel):
    """一次写操作的权限档判定结果（编排层契约，非冻结工具契约）。"""

    model_config = ConfigDict(extra="forbid")

    intent: str
    tier: str
    factors: list[str] = []
    escalation: int = 0
    requires_otp: bool = False
    to_human: bool = False
    delayed: bool = False             # L3：窗口内不执行，等 60s 或撤销
    source: str = ""                  # 档位算法的来源（审计可溯）


def requires_otp(tier: str) -> bool:
    """L2/L3 都要 OTP（§5 表：L2「确认卡 + OTP」、L3「双因子 + 延迟」）。"""
    return tier in ("L2", "L3")


def parse_iso(value: str):
    """ISO 时间串 → datetime；认不出返回 None（不抛）。"""
    from datetime import datetime
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def l3_window_elapsed(created_at: str, now_iso: str) -> bool:
    """L3 的 60s 延迟窗口是否已过（第 60 秒整即到期）。§5「延迟 60s 生效」。"""
    created, now = parse_iso(created_at), parse_iso(now_iso)
    if created is None or now is None:
        return False
    from datetime import timedelta
    return now - created >= timedelta(seconds=L3_DELAY_SECONDS)


def to_human(tier: str, factors: Iterable[str]) -> bool:
    """转人工：落到 L3，或降级因子命中 ≥2（§5 原文）。"""
    return tier == TIER_ORDER[-1] or len(list(factors)) >= MIN_FACTORS_FOR_HUMAN


def _verdict(intent: str, tier: str, factors: list[str], *, escalation: int = 0,
             source: str) -> TierVerdict:
    return TierVerdict(intent=intent, tier=tier, factors=list(factors), escalation=escalation,
                       requires_otp=requires_otp(tier), to_human=to_human(tier, factors),
                       delayed=tier == TIER_ORDER[-1], source=source)


def assess_write(intent: str, *, tier: str | None = None, factors: Iterable[str] | None = None,
                 escalation: int = 0) -> TierVerdict:
    """按规格 §5 给写意图定档。

    `tier` / `factors` 由调用方从**工具层预览**的事实包传入（转账类）—— 那是同一套 §5 内核的输出；
    取值本身仍全是代码算出来的，LLM 无从参与（铁律 1）。
    """
    hits = list(factors or [])
    if tier is not None:
        if tier not in TIER_ORDER:                                     # 工具层给的档位必须合法
            raise ValueError(f"未知权限档：{tier!r}")
        return _verdict(intent, tier, hits, escalation=escalation, source="tools._transfer_risk._assess")
    base = INTENT_BASE_TIERS.get(intent)
    if base is None:                                                   # fail-closed
        logger.warning("未登记的写意图按 L3 处理（fail-closed）：intent=%s", intent)
        return _verdict(intent, TIER_ORDER[-1], hits, escalation=escalation, source="fail_closed")
    return _verdict(intent, base, hits, escalation=escalation, source="spec_§5_table")


def assess_transfer(payee: dict, amount_cents: int, flows: list[dict], moment) -> TierVerdict:
    """转账类的档位判定：直接复用工具层 §5 内核（**唯一实现**），出参统一成 `TierVerdict`。

    用途有二：① 编排层在 CONFIRM_CARD 前自查一遍；② 单测用它钉「guard 与工具层预览同档」的一致性
    （两条路径必须给同一档，否则就是两份实现漂移）。
    """
    judged = _assess_transfer(payee, amount_cents, flows, moment)
    return _verdict("transfer_single", judged["tier"], judged["factors"],
                    escalation=judged.get("escalation", 0), source="tools._transfer_risk._assess")
