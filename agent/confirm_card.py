"""确认卡（卡 10 第 2 条）与确认流程的会话态（第 4/5 条）。

两件事，一个模块，因为它们是同一层「写操作确认」的两面：

1. **渲染**：`render(CardInput)` → 确认卡文本 = 标题 + 意图 + 卡面行 + 权限档 + 风险提示。
   卡上每个数字都来自**工具层的事实包**，本模块只做中文措辞与拼装 —— 不造数字（铁律 2）。
   card-24 起**卡面行由调用方给出**（写意图描述符，见 `agent/write_intents.py`），
   本模块不再硬编码「转账」字样，同一套模板服务所有写意图；
   转账的渲染结果与 card-10 **逐字一致**（既有用例是判据）。
2. **会话态**：一次写操作从「等确认」→「等 OTP」→「已执行」的阶段机；会话级 OTP 错误计数与锁定
   （错 `OTP_MAX_ERRORS` 次锁会话）；L3 的待复核登记 / 撤销 / 延迟放行。

⚠ OTP 的**校验位置分两种**（card-24 台账口径，由 `write_intents.WriteSpec.otp_in_tool` 声明）：
- `preview_token` 族（T7 转账）：校验与幂等**复用工具层**（`tools/transfer.execute_transfer`），
  本模块只数「错了几次」并锁会话，**不另写一份 OTP 比对**（analyst 裁决，card-10 台账）；
- `confirm_ref` 族（T11/T12/T15）：工具签名**没有 otp 参数** → 校验落在**编排层**
  （依据 `tools/cross_scene.py`「确认与 OTP 落在编排层（同 T11/T15 的分工）」），
  本模块同样只负责计数与锁定，比对由写意图描述符做。

⚠ 会话态在进程内存里（demo 口径）：进程重启即清空；多 worker 部署需要换成共享存储 —— 记在交付待拍板。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from guard import permission
from guard.permission import L3_DELAY_SECONDS


def _now() -> datetime:
    """当前时刻（测试用假时钟钉这里，跨天可复现）。"""
    return datetime.now()


#: 「确认」的认可词（严格小写比对前先 strip + lower；其余回复一律回到 SLOT_FILL）
CONFIRM_WORDS = ("确认", "确定", "同意", "可以", "好的", "是", "yes", "y", "ok")

#: 会话级 OTP 错误上限（来源：卡 10 第 4 条「错误 3 次锁定该会话」）
OTP_MAX_ERRORS = 3

#: 降级因子 → 客户可读的风险提示（只列 §5 的五个；未命中因子则给中性提示）
FACTOR_NOTES = {
    "night": "夜间时段（深夜至凌晨）转账",
    "velocity": "短时内多笔写操作",
    "amount_jump": "金额明显高于您近三个月的支出水平",
    "device_change": "设备变更",
    "geo_change": "常用位置变化",
    "new_payee": "首次向该收款人转账",
}

#: 卡面模板。`{rows}` 由调用方拼好的多行「标签：值」构成（card-24 起不再硬编码转账字段）。
CARD_TEMPLATE = """【{title}】
意图：{intent_cn}
{rows}
权限档：{tier}{otp_hint}
风险提示：{risk_note}
请回复「确认」继续；回复其它内容，我们重新填写。"""

PENDING_TEMPLATE = """【待人工复核】这笔{what}按规格 §5 属 L3：{reason}
编号：{pending_id}
将在 {delay_seconds} 秒后生效；回复「撤销 {pending_id}」可取消。"""


class CardInput(BaseModel):
    """确认卡的输入：卡面行里的数字一律来自工具层事实包。"""

    model_config = ConfigDict(extra="forbid")

    title: str                                  # 卡标题（不含书名号），如「转账确认卡」
    intent_cn: str
    rows: list[tuple[str, str]]                 # 卡面行（标签、值）；顺序即渲染顺序
    tier: str
    requires_otp: bool
    risk_note: str


def mask_phone(value: str) -> str:
    """手机号脱敏：已经是掩码（含 `*`）的原样返回；11 位号码保留前 3 后 4（铁律 8）。"""
    text = str(value)
    if "*" in text or len(text) != 11 or not text.isdigit():
        return text
    return f"{text[:3]}****{text[-4:]}"


def risk_note(factors: list[str], what: str = "转账") -> str:
    """降级因子 → 风险提示；无命中给中性提示（提示语本身不含业务数字）。"""
    notes = [FACTOR_NOTES[item] for item in factors if item in FACTOR_NOTES]
    return "；".join(notes) if notes else f"常规{what}，未触发风险因子"


def expected_arrival(tier: str) -> str:
    """预计到账：L3 要过人工复核，其余实时（demo 口径，§5 未定义，写进交付说明）。"""
    return "人工复核通过后实时到账" if tier == "L3" else "实时到账"


def render(card: CardInput) -> str:
    """渲染确认卡文本（占位符全部来自 `card`，缺字段由 Pydantic 拦下）。"""
    rows = "\n".join(f"{label}：{value}" for label, value in card.rows)
    return CARD_TEMPLATE.format(otp_hint="（需输入短信验证码）" if card.requires_otp else "",
                                rows=rows, **card.model_dump(exclude={"rows"}))


def is_confirmation(text: str) -> bool:
    """用户是否给出确认：去空白、转小写后**包含**认可词之一（不认模糊问答）。"""
    normalized = str(text).strip().lower()
    return any(word in normalized for word in CONFIRM_WORDS)


def otp_prompt(what: str = "转账") -> str:
    """等 OTP 的追问（不含任何业务数字）。"""
    return f"请输入短信验证码以完成这笔{what}。"


def otp_wrong_text(remaining: int) -> str:
    """OTP 错误的回执：剩余次数来自会话计数（代码算的），不是模型编的。"""
    return f"验证码不正确，还可再试 {remaining} 次。"


def otp_locked_text(what: str = "转账") -> str:
    """OTP 错误超限：锁定会话（卡 10 第 4 条）。"""
    return f"验证码连续错误次数过多，本次会话的{what}已被锁定，请稍后再试或联系客服。"


def to_human_text(factors: list[str]) -> str:
    """转人工：命中 ≥2 个降级因子（§5）。"""
    return f"这笔操作触发了 {len(factors)} 项风险因子，已转人工复核，请等待客服联系。"


def pending_text(pending_id: str, factors: list[str], what: str = "转账") -> str:
    """L3 待复核（延迟窗口 + 撤销入口）。"""
    return PENDING_TEMPLATE.format(reason=risk_note(factors, what), what=what, pending_id=pending_id,
                                   delay_seconds=L3_DELAY_SECONDS)


def undone_text(pending_id: str, what: str = "转账") -> str:
    return f"已取消编号 {pending_id} 的这笔{what}，没有扣款。"


# ---------------- 确认流程的会话态（进程内） ----------------

class Confirmation(BaseModel):
    """一次进行中的写操作确认（会话级）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    intent: str
    credential_kind: str              # "preview_token"（工具层预览签发）| "confirm_ref"（工具层私有约定签发）
    credential: str                   # 凭证值 —— **由工具层签发**，编排层不得自己造串（card-24 要求 3）
    target_id: str                    # 工具层主键：`payee_id` / `sub_id`
    target_name: str                  # 卡面主对象名：收款人 / 商户
    masked_phone: str = ""            # 转账专用（其余写意图留空）
    amount_yuan: str
    amount_cents: int
    tier: str
    requires_otp: bool
    stage: str = "confirm"            # confirm → otp → 完成/放弃
    card_text: str = ""


class Pending(BaseModel):
    """L3 待复核项（延迟窗口内可撤销）。"""

    model_config = ConfigDict(extra="forbid")

    pending_id: str
    session_id: str
    credential_kind: str
    credential: str
    target_id: str
    amount_cents: int
    created_at: str
    revoked: bool = False
    released: bool = False


_CONFIRMATIONS: dict[str, Confirmation] = {}
_OTP_ERRORS: dict[str, int] = {}
_LOCKED: set[str] = set()
_PENDING: dict[str, Pending] = {}


def reset_state() -> None:
    """清空会话态（测试与演示用）。"""
    _CONFIRMATIONS.clear()
    _OTP_ERRORS.clear()
    _LOCKED.clear()
    _PENDING.clear()


def start(confirmation: Confirmation) -> None:
    """登记一次待确认（同一会话同一时刻只有一个进行中的确认）。"""
    _CONFIRMATIONS[confirmation.session_id] = confirmation


def current(session_id: str) -> Confirmation | None:
    return _CONFIRMATIONS.get(session_id)


def clear(session_id: str) -> None:
    _CONFIRMATIONS.pop(session_id, None)


def to_otp_stage(session_id: str) -> None:
    """等确认 → 等 OTP。"""
    if (confirmation := _CONFIRMATIONS.get(session_id)) is not None:
        confirmation.stage = "otp"


def is_locked(session_id: str) -> bool:
    return session_id in _LOCKED


def otp_errors(session_id: str) -> int:
    return _OTP_ERRORS.get(session_id, 0)


def note_otp_error(session_id: str) -> int:
    """记一次 OTP 错误，返回**剩余**可试次数；用完则锁定会话。"""
    used = _OTP_ERRORS.get(session_id, 0) + 1
    _OTP_ERRORS[session_id] = used
    if used >= OTP_MAX_ERRORS:
        _LOCKED.add(session_id)
    return max(OTP_MAX_ERRORS - used, 0)


def unlock(session_id: str) -> None:
    """解锁（人工核实后走这里；本卡不提供自助解锁）。"""
    _LOCKED.discard(session_id)
    _OTP_ERRORS[session_id] = 0


def remember_pending(pending: Pending) -> None:
    _PENDING[pending.pending_id] = pending


def pending_of(pending_id: str, session_id: str) -> Pending | None:
    """取待复核项：必须属于该会话（别人的编号取不到）。"""
    item = _PENDING.get(pending_id)
    return item if item is not None and item.session_id == session_id else None


def revoke(pending_id: str, session_id: str) -> bool:
    """撤销入口：窗口内撤销成功 → True（**不执行、不扣款**）。"""
    item = pending_of(pending_id, session_id)
    if item is None or item.released:
        return False
    item.revoked = True
    return True


def due(pending_id: str, now_iso: str) -> bool:
    """L3 的 60s 延迟是否已过（窗口口径在 `guard.permission.l3_window_elapsed`）。"""
    item = _PENDING.get(pending_id)
    if item is None or item.revoked or item.released:
        return False
    return permission.l3_window_elapsed(item.created_at, now_iso)


def mark_released(pending_id: str) -> None:
    if (item := _PENDING.get(pending_id)) is not None:
        item.released = True


def now_iso() -> str:
    """当前时刻的 ISO 串（测试用假时钟钉 `_now`，跨天可复现）。"""
    return _now().isoformat(timespec="seconds")
