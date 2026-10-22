"""工具层 T17（卡 20）：收款人自助添加 —— `add_payee(name, phone)`。

规格 `docs/01-接口规格.md` §2（函数名与 `data` 的 3 个键都冻结）+ §5（`payee_add` → L1，不发确认卡、不要 OTP）。

铁律 8：**手机号一律脱敏** —— 完整号不落库、不回显、不进日志/审计。本模块的入参可以是完整 11 位数字
（用户从表单填的）或已是 `138****0001` 形式；落库 / 出参 / 回执里**只出现脱敏形式**。
（`data.masked_phone` 与种子里 `payee.phone` 的存法一致：那张表本来就只存脱敏串。）

去重口径（卡 20 第 6 条二选一，选前者）：同一 user 下**同名同手机号不重复插入** —— 返回既有收款人，
`ok=True`、`data` 仍严格只 3 个键，用 `message` 说明"已存在"（**不往 `data` 里加 `duplicate` 之类的新键**）。
"""

from __future__ import annotations

import datetime
import logging
import re
import threading
import uuid

from data import dao

from tools._query_common import (
    DEMO_USER_ID, ToolError, _dao_reject, _fail, _invalid, _ok, current_session_id,
    current_user_id, require_owned,
)
from tools.schemas import AddPayeeReq, ErrorCode, ToolResult

# ---------------- 状态工具再导出（T25/T28/T29 已拆到 tools.payee_status.py） ----------------
from tools.payee_status import (    # noqa: F401,E402 —— 保持 payee.set_payee_whitelist 等旧引用零改动
    UNBLACKLIST_ACTION,
    UNBLACKLIST_TIER,
    WHITELIST_ACTION,
    WHITELIST_TIER,
    check_confirm_ref,
    issue_confirm_ref,
    set_payee_blacklist,
    set_payee_whitelist,
    unset_payee_blacklist,
    whitelist_target,
)

logger = logging.getLogger(__name__)

#: 完整手机号：11 位数字（本 demo 全合成，**绝不落库**）
_FULL_PHONE = re.compile(r"^\d{11}$")
#: 已脱敏形式：前 3 位 + 4 个星号 + 后 4 位（铁律 8 的展示形态）
_MASKED_PHONE = re.compile(r"^\d{3}\*{4}\d{4}$")


def mask_phone(phone: str) -> str | None:
    """完整号 → 脱敏形式；已是脱敏形式则原样返回；两种都不是 → `None`（**不猜、不截断、不补位**）。"""
    text = (phone or "").strip()
    if _MASKED_PHONE.match(text):
        return text
    if _FULL_PHONE.match(text):
        return f"{text[:3]}****{text[-4:]}"
    return None


def _payload(row: dict) -> dict:
    """规格 §2 冻结的 `data`：**只这 3 个键**（新键会破坏契约，去重之类的说明走 `message`）。"""
    return {"payee_id": row["id"], "name": row["name"], "masked_phone": row["phone"]}


def _facts(row: dict) -> dict:
    """进事实包的数字：脱敏手机号里的数字会被回执引用，必须能在 facts 里找到（铁律 2）。"""
    return {"name": row["name"], "masked_phone": row["phone"]}


def _existing(user_id: str, name: str, masked: str) -> dict | None:
    """同一 user 下**同名同脱敏手机号**的既有收款人（DAO 的匹配是模糊的，这里收成精确比较）。"""
    for row in dao.find_payee(masked):
        if row["user_id"] == user_id and row["name"] == name and row["phone"] == masked:
            return row
    return None


def add_payee(name: str, phone: str) -> ToolResult:
    """T17 添加收款人（L1：只加联系人、不动钱 → 不发确认卡、不要 OTP）。`data` = `{payee_id, name, masked_phone}`。

    归属：只写当前会话用户（`set_current_user` 口径）；非本 demo 用户 → fail-closed `FORBIDDEN`。
    错误码：非空/格式不合法 → `INVALID_ARGUMENT`；DAO 拒绝 → `INVALID_ARGUMENT`；越权 → `FORBIDDEN`。
    """
    if (bad := _invalid(AddPayeeReq, name=name, phone=phone)) is not None:
        return bad
    clean_name = name.strip()
    masked = mask_phone(phone)
    if masked is None:                                   # 错误消息**不回显取值**：完整号绝不进日志
        logger.warning("手机号格式不合法（按铁律 8 不打印取值）：tool=add_payee")
        return _fail(ErrorCode.INVALID_ARGUMENT, "手机号格式不对：请填 11 位数字，或已脱敏的形式")
    user_id = current_user_id()
    try:                                                 # demo 期只有一个用户：非该身份一律 fail-closed
        require_owned("该用户的收款人列表", DEMO_USER_ID, user_id, tool="add_payee", intent="payee_add")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    if (row := _existing(user_id, clean_name, masked)) is not None:
        return _ok(_payload(row), _facts(row), f"{clean_name}（{masked}）已经在您的收款人里了")
    try:
        row = dao.insert_payee(user_id, clean_name, masked)
    except ValueError as exc:
        return _dao_reject(exc)
    return _ok(_payload(row), _facts(row), f"已添加 {clean_name}（{masked}）")


# ---------------- T20 list_payees（查看白名单 · SPEC-CHANGE 新增，L0 只读） ----------------

#: 归属过滤写在 SQL 里（fail-closed）；白名单优先 + 姓名/编号稳定排序，渲染层直接照抄顺序、不重排。
_PAYEE_SQL = "SELECT * FROM payee WHERE user_id = ? ORDER BY is_whitelist DESC, name, id"


def _rows(user_id: str) -> list[dict]:
    """当前 user 的收款人行。

    ⚠️ **为什么直查而不用 DAO 原语**：`data/dao.py` 已**正好 300 行**（`CLAUDE.md` 单文件红线），
    加不下 `list_payees`；且现有 `dao.find_payee` 的空关键词会抛 `ValueError`、也不做归属过滤。
    `tools/` 直查在仓内有先例（`tools/cross_scene.py:_savings_row`）。
    **补 DAO 原语并拆 `dao.py` 属另一张卡**，已在 PR 的「剩余限制」登记。
    """
    conn = dao.connection()
    return [dict(row) for row in conn.execute(_PAYEE_SQL, (user_id,))]


def _split_tags(raw: object) -> list[str]:
    """`tags` 列（逗号分隔）→ 去空白、去空项的列表；NULL/非法 → 空列表（不猜）。"""
    if not raw or not isinstance(raw, str):
        return []
    return [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]


def list_payees() -> ToolResult:
    """T20 列出当前用户的全部收款人（L0 只读）。`data` = `items[]` / `total_count` / `whitelist_count`。

    依据用户需求「新增一个查看白名单的功能，可以看到白名单与非白名单人员的手机号」。

    口径：
    - **只读当前 user**（他人的收款人一条都不能出现）—— 归属 fail-closed，越权 `FORBIDDEN`；
    - 手机号**照抄入库的脱敏值**（`data.masked_phone`），不拼接、不补全、**绝不还原完整号**（铁律 8）；
    - `is_whitelist` 一并返回；**分组文案由编排层渲染**，本层不做措辞。
    """
    user_id = current_user_id()
    try:                                            # demo 期只有一个用户：非该身份一律 fail-closed
        require_owned("该用户的收款人列表", DEMO_USER_ID, user_id,
                      tool="list_payees", intent="payee_list")
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    try:
        rows = _rows(user_id)
    except ValueError as exc:
        return _dao_reject(exc)
    items = [{"payee_id": row["id"], "name": row["name"], "masked_phone": row["phone"],
              "bank": row["bank"], "is_whitelist": bool(row["is_whitelist"])} for row in rows]
    whitelisted = [item for item in items if item["is_whitelist"]]
    #: v2（转账域串联）：标签 / 黑名单 / 专属额度需要给**编排层**的解析关卡用。
    #: **只放 facts，不进 data** —— `data.items` 是冻结契约（5 个键），加字段会破契约；
    #: 而 facts 本就是「给编排层的额外事实包」，加键是既有做法（facts 不受冻结约束）。
    profiles = {row["id"]: {"tags": _split_tags(row["tags"]),
                            "blacklist": bool(row["blacklist"]),
                            "blacklist_reason": row["blacklist_reason"],
                            "whitelist_quota": row["whitelist_quota"]} for row in rows}
    facts = {"total_count": len(items), "whitelist_count": len(whitelisted),
             "non_whitelist_count": len(items) - len(whitelisted), "items": items,
             "profiles": profiles}
    data = {"items": items, "total_count": len(items), "whitelist_count": len(whitelisted)}
    return _ok(data, facts, f"您共有 {len(items)} 位收款人，其中白名单 {len(whitelisted)} 位。")


