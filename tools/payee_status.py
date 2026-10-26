"""收款人**状态**工具族（T25 白名单 / T28 加黑 / T29 解除黑名单）。

2026-10-22 从 `tools/payee.py` 拆出（原文件 349 行，超 300 红线）：
- 本模块收 T25/T28/T29 三个**状态写工具** + 它们共用的 `issue_confirm_ref` / `check_confirm_ref`
  凭证机制（工具层私有约定，不属于规格 §2 冻结的工具函数，口径见原注释）；
- `tools/payee.py` 保留 T17/T20，并在顶部再导出本模块的公开名 —— 旧引用
  `payee.set_payee_whitelist(...)` 等**零改动**。

分层：本模块属工具层，可调 guard/data；不新增任何规格 §2 契约。
"""
from __future__ import annotations

import datetime
import re
import threading
import uuid

from data import dao, payee_dao

from tools._query_common import (
    DEMO_USER_ID, ToolError, _fail, _ok, current_session_id, current_user_id, require_owned,
)
from tools.schemas import ErrorCode, ToolResult

#: 白名单有效期的档位与工具名（**仅展示、不参与风控** —— 人类明确口径）
EXPIRE_TIER, EXPIRE_TOOL = "L1", "set_whitelist_expire"


def set_whitelist_expire(payee_id: str, expire_date: str) -> ToolResult:
    """T36 设白名单有效期（**仅展示**，不参与任何风控判定）。`data` = `{payee_id, whitelist_expire}`。

    空串 = 清除（长期有效）。要求该收款人**在白名单里**（否则 `INVALID_STATE`：有效期属于白名单属性）。
    L1：只是一条展示用元数据，不动钱、不改白名单资格。
    """
    value = str(expire_date or "").strip()
    if value:
        try:
            value = datetime.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return _fail(ErrorCode.INVALID_ARGUMENT, "有效期格式不对，请用 2026-12-31 这样的日期")
    user_id = current_user_id()
    try:
        require_owned("该收款人", DEMO_USER_ID, user_id, tool=EXPIRE_TOOL, intent="payee_whitelist")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    row = dao.connection().execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
    if not row["is_whitelist"]:
        return _fail(ErrorCode.INVALID_STATE, "该收款人不在白名单里，谈不上有效期")
    from data.db import transaction
    with transaction(dao.connection()):
        dao.connection().execute("UPDATE payee SET whitelist_expire = ? WHERE id = ? AND user_id = ?",
                                 (value or None, payee_id, user_id))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="payee_whitelist", tool=EXPIRE_TOOL,
                         params_json={"payee_id": payee_id, "expire": value or None},
                         risk_level=EXPIRE_TIER, permission_tier=EXPIRE_TIER,
                         result="success", error_code=None)
    label = f"白名单有效期到 {value}" if value else "白名单长期有效"
    return _ok({"payee_id": payee_id, "whitelist_expire": value or None},
               {"name": row["name"], "whitelist_expire": value or ""}, f"{row['name']}：{label}")


# ---------------- T25 set_payee_whitelist（SPEC-CHANGE 新增 · L2） ----------------
#
# 为什么必须是 L2 + 验证码（不是 L1）：**白名单 = 转账免 OTP 的特权**。若"加收款人"（L1、不要
# OTP）能顺带把对方设成白名单，那么账户一旦被盗，攻击者只要把自己加成白名单，就能**永久绕过
# OTP**。所以「授予这个特权」必须单独验证身份 —— 这也是人类批准本卡时给的档位。

#: 白名单写入档位（§5 SPEC-CHANGE）：L2 + OTP（编排层校验验证码，同 T11/T15 的 confirm_ref 族）
WHITELIST_TIER = "L2"
#: 凭证绑定的 action 名（与工具名一致）
WHITELIST_ACTION = "set_payee_whitelist"
#: 凭证 TTL（同 T11/T15 口径：自包含 ref，300 秒）
CONFIRM_TTL_SECONDS = 300

_CONFIRM_REFS: dict[str, dict] = {}
#: 幂等临界区：校验凭证 → 执行 → 写快照整体串行（同 ref 并发调用绝不重复执行）
_CONFIRM_LOCK = threading.Lock()


def issue_confirm_ref(action: str, target_id: str) -> str:
    """签发确认凭证（**工具层私有约定**，不属于规格 §2 冻结的 25 个工具函数）。

    编排层渲染确认卡、用户确认后调本函数，把返回的 ref 交给 `set_payee_whitelist`。
    绑定 `action + target_id + 当前用户 + 签发时刻`，TTL `CONFIRM_TTL_SECONDS`，一次性消费。
    """
    if not str(action or "").strip() or not str(target_id or "").strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "action 与 target_id 都不能为空")
    ref = f"cf_{uuid.uuid4().hex}"
    _CONFIRM_REFS[ref] = {"action": action, "target_id": target_id, "user_id": current_user_id(),
                          "created_at": datetime.datetime.now(), "snapshot": None}
    return ref


def check_confirm_ref(confirm_ref: str, action: str, target_id: str) -> dict:
    """校验并取回凭证（不消费）。**非法一律 `raise ToolError`**（同 `tools/subscription.py` 口径）。

    错误码边界：不存在 / 非本人 / 绑定不符 → `FORBIDDEN`；存在但超 TTL → `TOKEN_EXPIRED`。
    """
    ref = _CONFIRM_REFS.get(confirm_ref)
    if ref is None:
        raise ToolError(ErrorCode.FORBIDDEN, "确认凭证无效：不是本会话签发的凭证")
    if ref["user_id"] != current_user_id():
        raise ToolError(ErrorCode.FORBIDDEN, "该确认凭证不属于当前用户")
    if ref["action"] != action or ref["target_id"] != target_id:
        raise ToolError(ErrorCode.FORBIDDEN, "确认凭证与本次操作不匹配")
    if datetime.datetime.now() - ref["created_at"] > datetime.timedelta(seconds=CONFIRM_TTL_SECONDS):
        _CONFIRM_REFS.pop(confirm_ref, None)
        raise ToolError(ErrorCode.TOKEN_EXPIRED, "确认凭证已过期，请重新确认")
    return ref


def whitelist_target(payee_id: str, enable: bool) -> str:
    """凭证绑定用的 target：**把方向也绑进去**，防止拿「加入」的凭证去执行「移出」。"""
    return f"{payee_id}:{int(bool(enable))}"


def _whitelist_payload(row: dict, is_whitelist: bool) -> tuple[dict, dict]:
    data = {"payee_id": row["id"], "name": row["name"], "masked_phone": row["phone"],
            "is_whitelist": is_whitelist}
    return data, {**data}


def set_payee_whitelist(payee_id: str, enable: bool, confirm_ref: str) -> ToolResult:
    """T25 设置收款人白名单（L2）。`data` = `{payee_id, name, masked_phone, is_whitelist}`。

    归属 fail-closed；凭证绑定方向（`payee_id:1/0`）；同一 ref 重复调用返回既有结果（幂等）。
    写入与审计同一事务（铁律 5）；**脱敏手机号原样照抄入库值**，绝不还原完整号（铁律 8）。
    """
    user_id = current_user_id()
    try:
        require_owned("该收款人", DEMO_USER_ID, user_id,
                      tool=WHITELIST_ACTION, intent="payee_whitelist")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    row = dao.connection().execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
    row = dict(row)
    target = whitelist_target(payee_id, enable)
    with _CONFIRM_LOCK:
        try:
            rec = check_confirm_ref(confirm_ref, WHITELIST_ACTION, target)
        except ToolError as exc:                           # 错误码边界见 check_confirm_ref
            return _fail(exc.code, exc.message)
        if rec["snapshot"] is not None:                    # 幂等：同一 ref 重复调用
            data, facts = _whitelist_payload(row, bool(rec["snapshot"]["is_whitelist"]))
            return _ok(data, facts, "白名单状态已更新（重复提交，未重复写入）")
        flag = 1 if enable else 0
        from data.db import transaction                    # 局部 import：与 tools 其余模块同口径
        conn = dao.connection()
        with transaction(conn):
            #: 2026-10-26（人类要求）：三种身份**互斥且可自由切换** ——
            #: 设为白名单时顺带解除黑名单（并清原因），否则会出现「既白又黑」的矛盾状态。
            conn.execute(
                "UPDATE payee SET is_whitelist = ?,"
                " blacklist = CASE WHEN ? = 1 THEN 0 ELSE blacklist END,"
                " blacklist_reason = CASE WHEN ? = 1 THEN NULL ELSE blacklist_reason END"
                " WHERE id = ? AND user_id = ?",
                (flag, flag, flag, payee_id, user_id))
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="payee_whitelist", tool=WHITELIST_ACTION,
                             params_json={"payee_id": payee_id, "is_whitelist": bool(flag)},
                             risk_level=WHITELIST_TIER, permission_tier=WHITELIST_TIER,
                             result="success", error_code=None)
        rec["snapshot"] = {"is_whitelist": bool(flag),
                           "cleared_blacklist": bool(flag and row.get("blacklist"))}
    data, facts = _whitelist_payload(row, bool(flag))
    word = "已加入白名单" if flag else "已移出白名单"
    return _ok(data, facts, f"{row['name']}（{row['phone']}）{word}")



# ---------------- T28 set_payee_blacklist（SPEC-CHANGE 新增 · L1） ----------------
#
# 档位口径：加黑是「阻断」（不许转账/送礼），不是「授权」，风险低于加白；且人类需求只要求
# 「解除黑名单」要验证码、没要求「加黑」要验证码 → 故 L1、不发 OTP（与 payee_add 同档）。

def set_payee_blacklist(payee_id: str, reason: str = "") -> ToolResult:
    """T28 加入黑名单（L1：只改状态、不动钱）。`data` = `{payee_id, name, masked_phone, blacklist}`。

    归属 fail-closed；与白名单**互斥**（置 blacklist=1 时清 is_whitelist）；
    已在黑名单 → 幂等返回（不重复写审计）。
    """
    user_id = current_user_id()
    try:
        require_owned("该收款人", DEMO_USER_ID, user_id,
                      tool="set_payee_blacklist", intent="payee_blacklist")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    conn = dao.connection()
    row = conn.execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
    row = dict(row)
    data = {"payee_id": row["id"], "name": row["name"], "masked_phone": row["phone"],
            "blacklist": True}
    if row["blacklist"]:
        return _ok(data, {"name": row["name"], "masked_phone": row["phone"]},
                   f"{row['name']}（{row['phone']}）已经在黑名单里了")
    from data.db import transaction                    # 局部 import：与 T25 同口径
    with transaction(conn):
        conn.execute("UPDATE payee SET blacklist = 1, blacklist_reason = ?, is_whitelist = 0"
                     " WHERE id = ? AND user_id = ?", ((reason or "").strip(), payee_id, user_id))
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="payee_blacklist", tool="set_payee_blacklist",
                         params_json={"payee_id": payee_id, "reason": (reason or "").strip()},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    return _ok(data, {"name": row["name"], "masked_phone": row["phone"]},
               f"{row['name']}（{row['phone']}）已加入黑名单")


# ---------------- T29 unset_payee_blacklist（SPEC-CHANGE 新增 · L2） ----------------
#
# 档位口径：人类需求原文「解除黑名单必须通过验证码确认」→ **L2 + OTP**（confirm_ref 族，
# 与 T25 同构）。加黑是 L1（阻断）、解除是 L2（恢复特权），不对称是刻意的。

#: 解除黑名单的档位与凭证 action（与 T25 同族口径）
UNBLACKLIST_TIER = "L2"
UNBLACKLIST_ACTION = "unset_payee_blacklist"


def unset_payee_blacklist(payee_id: str, confirm_ref: str) -> ToolResult:
    """T29 解除黑名单（L2：凭证必须来自确认卡 + OTP）。`data` = `{payee_id, name, masked_phone, blacklist}`。

    不在黑名单 → `INVALID_STATE`（幂等不是静默成功）；凭证绑定 `payee_id:unblacklist`；
    同一 ref 重复调用幂等返回。
    """
    user_id = current_user_id()
    try:
        require_owned("该收款人", DEMO_USER_ID, user_id,
                      tool=UNBLACKLIST_ACTION, intent="payee_unblacklist")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    conn = dao.connection()
    row = conn.execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        return _fail(ErrorCode.NOT_FOUND, "找不到该收款人")
    row = dict(row)
    if not row["blacklist"]:
        return _fail(ErrorCode.INVALID_STATE, "该收款人不在黑名单里")
    target = f"{payee_id}:unblacklist"
    with _CONFIRM_LOCK:
        try:
            rec = check_confirm_ref(confirm_ref, UNBLACKLIST_ACTION, target)
        except ToolError as exc:
            return _fail(exc.code, exc.message)
        if rec["snapshot"] is not None:                    # 幂等：同一 ref 重复调用
            return _ok({"payee_id": row["id"], "name": row["name"],
                        "masked_phone": row["phone"], "blacklist": False},
                       {"name": row["name"], "masked_phone": row["phone"]},
                       "已解除黑名单（重复提交，未重复写入）")
        from data.db import transaction                    # 局部 import：与 T25/T28 同口径
        with transaction(conn):
            conn.execute("UPDATE payee SET blacklist = 0, blacklist_reason = NULL"
                         " WHERE id = ? AND user_id = ?", (payee_id, user_id))
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="payee_unblacklist", tool=UNBLACKLIST_ACTION,
                             params_json={"payee_id": payee_id},
                             risk_level=UNBLACKLIST_TIER, permission_tier=UNBLACKLIST_TIER,
                             result="success", error_code=None)
        rec["snapshot"] = {"blacklist": False}
    return _ok({"payee_id": row["id"], "name": row["name"],
                "masked_phone": row["phone"], "blacklist": False},
               {"name": row["name"], "masked_phone": row["phone"]},
               f"{row['name']}（{row['phone']}）已解除黑名单")

# ---------------- T45 备注 / T46 专属额度（2026-10-24 追加） ----------------

NOTE_TOOL, QUOTA_TOOL = "set_payee_note", "set_whitelist_quota"

#: 备注/自由文本里的完整手机号（11 位数字）→ 脱敏（铁律 8：完整号码绝不入库、绝不回显）
_PHONE_LIKE = re.compile(r"\d{11}")


def _mask_phones(text: str) -> str:
    """把 11 位连续数字按 `138****5678` 脱敏（只保留前 3 后 4）。"""
    return _PHONE_LIKE.sub(lambda m: f"{m.group()[:3]}****{m.group()[-4:]}", text)



def _owned_payee(payee_id: str, *, tool: str) -> dict:
    """取本人收款人（越权 fail-closed）；找不到 → NOT_FOUND。"""
    user_id = current_user_id()
    try:
        require_owned("该收款人", DEMO_USER_ID, user_id, tool=tool, intent="payee_list")
    except ToolError:
        raise
    row = dao.connection().execute("SELECT * FROM payee WHERE id = ?", (payee_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise ToolError(ErrorCode.NOT_FOUND, "找不到该收款人")
    return dict(row)


def set_payee_note(payee_id: str, note: str) -> ToolResult:
    """T45 设置/修改收款人**备注**（L1：纯说明文字，不参与任何风控判定）。

    空串 = 清除备注。`data` = `{payee_id, note}`。
    """
    text = _mask_phones(str(note or "").strip())[:60]        # 备注只做提示；**完整手机号一律脱敏**（铁律 8）
    try:
        row = _owned_payee(payee_id, tool=NOTE_TOOL)
        payee_dao.update_payee(payee_id, current_user_id(), note=text or None)
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="payee_note", tool=NOTE_TOOL, params_json={"payee_id": payee_id},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    label = f"备注已设为「{text}」" if text else "备注已清除"
    return _ok({"payee_id": payee_id, "note": text or ""},
               {"name": row["name"], "note": text}, f"{row['name']}：{label}")


def set_whitelist_quota(payee_id: str, quota_cents: int) -> ToolResult:
    """T46 设白名单**专属额度**（单位分；`0` = 清除，等同未设 → 一律要验证码）。

    L1 工具（真正的「修改白名单信息」在编排层按 **L2 + 验证码** 走，见 `payee_whitelist_edit`）。
    要求该人**在白名单里**（否则 `INVALID_STATE`）；负数 → `INVALID_ARGUMENT`。
    """
    if int(quota_cents) < 0:
        return _fail(ErrorCode.INVALID_ARGUMENT, "专属额度不能是负数")
    from data.db import transaction                       # 局部 import：与文件内其余函数同口径
    try:
        row = _owned_payee(payee_id, tool=QUOTA_TOOL)
        if not row["is_whitelist"]:
            return _fail(ErrorCode.INVALID_STATE, "该收款人不在白名单里，谈不上专属额度")
        value = int(quota_cents) or None
        with transaction(dao.connection()):
            payee_dao.update_payee(payee_id, current_user_id(), whitelist_quota=value)
            dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                             intent="payee_whitelist_edit", tool=QUOTA_TOOL,
                             params_json={"payee_id": payee_id, "quota_cents": value},
                             risk_level="L1", permission_tier="L1", result="success",
                             error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    label = f"专属额度已设为 {int(quota_cents) / 100:.2f} 元" if quota_cents else "专属额度已清除"
    return _ok({"payee_id": payee_id, "whitelist_quota": value},
               {"name": row["name"], "whitelist_quota": value}, f"{row['name']}：{label}")

# ---------------- T47 标签（2026-10-24 追加：人类原文「添加收款人时增加标签字段」） ----------------

TAGS_TOOL = "set_payee_tags"
#: 单个标签最长 8 字、最多 5 个（标签是**指代用的短词**，长了反而不好用）
TAG_MAX_CHARS, TAG_MAX_COUNT = 8, 5


def set_payee_tags(payee_id: str, tags: list[str]) -> ToolResult:
    """T47 设置/修改收款人**标签**（L1：标签影响「用标签指代人员」的解析，不动钱）。

    `tags` 为空列表 = 清空标签。返回 `data = {payee_id, tags}`。
    约束：去空白、去重、去空项；单标签 ≤ 8 字、最多 5 个（超了 → `INVALID_ARGUMENT`）。
    """
    cleaned: list[str] = []
    for raw in tags or []:
        tag = str(raw or "").strip().replace(",", " ").strip()
        if not tag or tag in cleaned:
            continue
        if len(tag) > TAG_MAX_CHARS:
            return _fail(ErrorCode.INVALID_ARGUMENT, f"标签「{tag}」太长（最多 {TAG_MAX_CHARS} 字）")
        cleaned.append(tag)
    if len(cleaned) > TAG_MAX_COUNT:
        return _fail(ErrorCode.INVALID_ARGUMENT, f"标签最多 {TAG_MAX_COUNT} 个")
    try:
        row = _owned_payee(payee_id, tool=TAGS_TOOL)
        payee_dao.update_payee(payee_id, current_user_id(),
                               tags=",".join(cleaned) if cleaned else None)
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="payee_tags", tool=TAGS_TOOL, params_json={"payee_id": payee_id},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    label = "、".join(cleaned) if cleaned else "（已清空）"
    return _ok({"payee_id": payee_id, "tags": cleaned},
               {"name": row["name"], "tags": cleaned}, f"{row['name']}：标签为 {label}")


# ---------------- T50 银行卡号（2026-10-24：人类规格「添加收款人卡片：银行卡号」） ----------------

CARD_NO_TOOL = "set_payee_card_no"
#: 卡号只留后四位：`**** **** **** 8888`（**完整卡号绝不入库、绝不回显**，铁律 8）
_CARD_NO_DIGITS = re.compile(r"\d{12,19}")


def set_payee_card_no(payee_id: str, card_no: str) -> ToolResult:
    """T50 记录收款人**银行卡号（脱敏形态）**。L1：只是展示用信息，不参与风控。

    入参可以是完整卡号，但**入库前一律脱敏**成 `**** **** **** 8888`；空串 = 清除。
    返回 `data = {payee_id, card_no}`（脱敏形态）。
    """
    digits = "".join(ch for ch in str(card_no or "") if ch.isdigit())
    if digits and not (12 <= len(digits) <= 19):
        return _fail(ErrorCode.INVALID_ARGUMENT, "银行卡号一般是 12-19 位数字，请核对")
    masked = f"**** **** **** {digits[-4:]}" if digits else ""
    try:
        row = _owned_payee(payee_id, tool=CARD_NO_TOOL)
        payee_dao.update_payee(payee_id, current_user_id(), card_no=masked or None)
        dao.insert_audit(f"trace-{uuid.uuid4().hex[:12]}", current_session_id(), actor="agent",
                         intent="payee_card_no", tool=CARD_NO_TOOL,
                         params_json={"payee_id": payee_id},
                         risk_level="L1", permission_tier="L1", result="success", error_code=None)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    label = f"卡号已记为 {masked}" if masked else "卡号已清除"
    return _ok({"payee_id": payee_id, "card_no": masked},
               {"name": row["name"], "card_no": masked}, f"{row['name']}：{label}")
