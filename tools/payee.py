"""工具层 T17（卡 20）：收款人自助添加 —— `add_payee(name, phone)`。

规格 `docs/01-接口规格.md` §2（函数名与 `data` 的 3 个键都冻结）+ §5（`payee_add` → L1，不发确认卡、不要 OTP）。

铁律 8：**手机号一律脱敏** —— 完整号不落库、不回显、不进日志/审计。本模块的入参可以是完整 11 位数字
（用户从表单填的）或已是 `138****0001` 形式；落库 / 出参 / 回执里**只出现脱敏形式**。
（`data.masked_phone` 与种子里 `payee.phone` 的存法一致：那张表本来就只存脱敏串。）

去重口径（卡 20 第 6 条二选一，选前者）：同一 user 下**同名同手机号不重复插入** —— 返回既有收款人，
`ok=True`、`data` 仍严格只 3 个键，用 `message` 说明"已存在"（**不往 `data` 里加 `duplicate` 之类的新键**）。
"""

from __future__ import annotations

import logging
import re

from data import dao

from tools._query_common import (
    DEMO_USER_ID, ToolError, _dao_reject, _fail, _invalid, _ok, current_user_id, require_owned,
)
from tools.schemas import AddPayeeReq, ErrorCode, ToolResult

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
