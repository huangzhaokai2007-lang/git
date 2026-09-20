"""卡 20：收款人自助添加的**编排层**入口（表单提交走这里，界面不得直连 `tools/`）。

两条路径分开：
- 聊天触发：`orchestrator.handle()` 判出 `payee_add` → 只回一句引导语 + 让界面弹表单（**不写库**）；
- 表单提交：`submit_payee(name, phone, session_id=...)` → 真正落库并返回回执。

为什么提交路径**没有**确认卡 / OTP（规格 §5 已定档，人类已批）：`payee_add` → **L1**，且
「表单提交本身即用户确认」—— 本操作只加联系人、不动钱（写操作四步里的 preview / 确认卡 / OTP
对它不适用）。档位仍由 `guard.permission` 的同一个内核算（§5 的取值经 `tier=` 传入，判档逻辑不分叉）。

分层：本模块属编排层，只调 `guard/`、`tools/`（+ 审计用的 `data.dao`，与 `orchestrator` 同口径）；
`interfaces/web/` 只调本模块。手机号在这里**原样传给工具层**，工具层负责脱敏 —— 本模块不打印、不落库它。
"""

from __future__ import annotations

import logging
import uuid

from data import dao

from agent import templates
from guard import facts_check, permission
from tools import payee as payee_tool
from tools.schemas import ToolResult

logger = logging.getLogger(__name__)

INTENT = "payee_add"
#: 规格 §5：`payee_add` → L1。guard 的 `INTENT_BASE_TIERS` 里没有它（`guard/` 不在卡 20 范围），
#: 所以这里把 §5 的取值显式交给 guard 的同一个内核 —— 判档仍只有 guard 一份实现（铁律 1）。
PAYEE_ADD_TIER = "L1"
#: 聊天触发时回的引导语（**不写库**；界面见到 `turn.intent == "payee_add"` 就弹表单）
PROMPT = "好的，请在下面的表单里填写收款人的姓名和手机号，提交后我立刻帮您加上。"


def _audit(trace_id: str, session_id: str, tier: str, result: str,
           error_code: str | None, params: dict) -> None:
    """写一条审计（铁律 5）。`params` **只放脱敏后的字段**：完整手机号绝不进审计。"""
    try:
        dao.insert_audit(trace_id, session_id, actor="agent", intent=INTENT, tool="add_payee",
                         params_json={"intent": INTENT, **params}, risk_level=tier,
                         permission_tier=tier, result=result, error_code=error_code)
    except Exception:                                    # noqa: BLE001 —— 审计隔离于回执（同 orchestrator）
        logger.exception("写审计失败（回执照常返回，但必须排查）：trace=%s", trace_id)


def _reply(result: ToolResult) -> tuple[str, bool, str | None]:
    """把工具结果变成回执：`(reply, degraded, error_code)`。回执里的每个数字都来自事实包（铁律 2）。"""
    if not result.ok:
        code = result.error_code if isinstance(result.error_code, str) else None
        return templates.tool_error(code, result.message), False, code
    name, masked = str(result.data["name"]), str(result.data["masked_phone"])
    if "已经" in result.message:                          # 去重：返回既有收款人
        text = f"{name}（{masked}）已经在您的收款人里了。现在可以给他转账了。"
    else:
        text = f"已添加 {name}（{masked}）。现在可以给他转账了。"
    if templates.verify_numbers(text, result.facts):       # §7 数字校验器（同一条防线）
        logger.warning("添加收款人回执出现 facts 之外的数字，降级为不含数字的措辞：%s", name)
        return f"已添加 {name}。", True, facts_check.ERROR_CODE
    return text, False, None


def submit_payee(name: str, phone: str, *, session_id: str) -> "Turn":
    """表单提交入口（界面只调这一个函数）：PRECHECK（guard 定档）→ EXECUTE → 校验回执 → AUDIT。

    返回编排层的 `Turn`：`intent=payee_add` / `tool_calls=["add_payee"]` / `tier="L1"` /
    `executed` 仅当工具成功落库才为 True。**不新增任何 Turn 字段**（界面靠 `intent` 与 `reply` 渲染）。
    """
    from agent.orchestrator import Turn                  # 局部 import：避免 orchestrator ↔ payee_flow 循环

    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    verdict = permission.assess_write(INTENT, tier=PAYEE_ADD_TIER)
    states = ["IDLE", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]
    result = payee_tool.add_payee(name, phone)
    reply, degraded, error_code = _reply(result)
    _audit(trace_id, session_id, verdict.tier,
           "success" if result.ok else "error", error_code,
           {"name": str(result.data["name"]) if result.ok else name,
            "masked_phone": str(result.data["masked_phone"]) if result.ok else ""})
    return Turn(trace_id=trace_id, intent=INTENT, confidence=1.0, states=states,
                tool_calls=["add_payee"], reply=reply, degraded=degraded, error_code=error_code,
                tier=verdict.tier, executed=result.ok)
