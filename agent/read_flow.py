"""编排层**只读路径的槽位填充与定档**（从 `agent/orchestrator.py` 拆出）。

为什么单开一个文件：`orchestrator.py` 在 F10 接通后到 310 行（红线 300，CLAUDE.md 第 51 行）。
本模块装的是**只读路径专属的纯函数** —— 槽位补全（时间口径 + F10 的问卷答案解析）与权限定档；
它们都不碰状态机、不碰审计，是天然可独立的一层。照 `agent/period.py`（卡 17b）与
`agent/read_routes.py`（卡 23）的先例拆出，`orchestrator` 反向 import（同一份实现，不是副本）。

分层：属编排层，只 import `agent/` 与 `tools/`；不碰 `data/`、不写 SQL。
"""

from __future__ import annotations

from typing import Mapping

from agent import risk_flow
from agent.period import anchor_period, resolve_day, resolve_period

# ---------------- 阈值与契约常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
READ_TIER = "L0"              # 来源：规格 §2 的只读意图权限档均为 L0
RANGE_FIELDS = ("date_from", "date_to")
#: 分析类意图：period 的缺省/解析口径（**完全没给**时取锚点当月；给了却认不出 → 追问，卡 17b）
PERIOD_INTENTS = ("bill_analysis", "anomaly_check", "bill_report")
#: 缺槽即追问（代码判定）：`txn_query` 的显式区间不猜；分析类意图的 `period` 认不出也不猜；
#: F10 的 `answers` 解析不出任何一题时也追问（提示用户把 5 题一并说出来）。
REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {"txn_query": RANGE_FIELDS,
                                             **{intent: ("period",) for intent in PERIOD_INTENTS},
                                             "risk_assess": ("answers",)}


#: 没给时间就**必须追问**的分析意图（2026-10-27 验收口径）。
#: 异常分析是"这段时间账目有没有问题"，**范围本身就是结论的一部分** —— 悄悄按锚点当月算，
#: 用户会以为查的是全部历史，而报告里只有本月几笔（实机就是这样被误解的）。
#: 账单分析/账单总结不在其中：说「花了多少」时默认本月是用户能接受的惯例。
PERIOD_MUST_BE_STATED = ("anomaly_check",)


def fill_period_slot(filled: dict, intent: str = "") -> None:
    """period 槽位：**没给** → 锚点当月（卡 09 口径，未变）；**给了但认不出** → 清掉（→ CLARIFY）。

    `PERIOD_MUST_BE_STATED` 里的意图例外：没给时**不默认**，留空 → 被 `REQUIRED_SLOTS`
    判成缺失 → 编排层追问时间段。
    """
    raw = filled.get("period")
    if raw in (None, "") and intent in PERIOD_MUST_BE_STATED:
        filled.pop("period", None)
        return
    resolved = anchor_period() if raw in (None, "") else resolve_period(raw)
    if resolved is None:
        filled.pop("period", None)
    else:
        filled["period"] = resolved


def _fill_risk_answers(filled: dict) -> None:
    """F10：`answers` 是**一句话**（LLM 抽的槽位）时，在**代码里**解析成 5 题。

    不让 LLM 抽数字、更不让它算分（铁律 1）。已经是 dict 的原样留着。
    """
    raw = filled.get("answers")
    if isinstance(raw, str):
        filled["answers"] = risk_flow.parse_answers(raw)


def _risk_missing(filled: dict) -> list[str]:
    """F10：`answers` 这个键**在**不等于"五题齐了"——说了一部分时它也是个非空 dict。

    故按工具层的五题契约再查一遍，缺的题按**中文短名**报出来（比报一个 `answers` 有用得多，
    用户才知道还差哪几题）。
    """
    return risk_flow.missing_questions(filled.get("answers") or {})


def fill_slots(intent: str, slots: Mapping) -> tuple[dict, list[str]]:
    """代码补齐时间槽位（+ F10 的问卷答案），然后算缺失项（缺 → CLARIFY）。"""
    filled = {key: value for key, value in slots.items() if value not in (None, "")}
    if intent in PERIOD_INTENTS:
        fill_period_slot(filled, intent)
    if intent == "txn_query":
        for field, edge in (("date_from", "start"), ("date_to", "end")):
            resolved = resolve_day(filled.get(field), edge=edge)
            if resolved is None:
                filled.pop(field, None)
            else:
                filled[field] = resolved
    if intent == risk_flow.INTENT:
        _fill_risk_answers(filled)
    missing = [name for name in REQUIRED_SLOTS.get(intent, ()) if not filled.get(name)]
    if intent == risk_flow.INTENT and not missing:
        missing = _risk_missing(filled)
    return filled, missing


def precheck(intent: str, read_intents: tuple[str, ...]) -> str:
    """权限预检（代码定档）：只读意图恒 `L0`，其余给 `L1+`（写操作与风控因子属卡 10/12）。

    `read_intents` 由调用方传入 —— 只读意图清单的唯一来源仍是 `orchestrator.READ_INTENTS`
    （本模块不复制一份，避免两处漂移）。
    """
    return READ_TIER if intent in read_intents else "L1+"
