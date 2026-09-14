"""工具层共享 helpers（任务卡 04b）：T1–T9 各模块共用的薄封装 + L2 归属断言，**单份实现**。

依赖方向**单向**：本模块只 import 标准库 + `data/`（DAO）+ `tools.schemas`；
**严禁**反向 import `tools.query` / `tools.transfer`（否则形成回边，卡 04b 明文禁止）。
调用方一律 `from tools._query_common import ...` 复用，不得再复制本地副本。

本模块不做任何业务/权限档/风控判断（那是 `guard/` 的活），只提供：
- `ToolError`：工具内部受控失败（由公开函数转成 `ToolResult(ok=False)`）；
- 会话用户上下文（`set_current_user` / `current_user_id`）与归属断言 `require_owned`；
- `_ok / _fail / _invalid / _dao_reject` 四种返回构造；
- 金额展示（`_money / _money_facts`，纯整数分，禁浮点）与 `_owned_account_ids`。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError

from data import dao
from data.seed import USER_ID as DEMO_USER_ID
from tools.schemas import ACCOUNT_TYPES, ErrorCode, ToolResult

logger = logging.getLogger(__name__)

#: 分 → 元 的换算基数（`_money` 专用）。与 `PCT_TOTAL` 数值同为 100 但**语义无关**，故各自命名：
#: 卡 04b 清理时发现旧 `_money` 借用了百分数基数常量，两份副本已在此处收敛成单份。
CENTS_PER_YUAN = 100
#: 百分数归一化基数（整数百分比；`query._split_pct` / `query._vs_prev_pct` 用）。
PCT_TOTAL = 100

_SESSION_USER: str | None = None


class ToolError(Exception):
    """工具内部的受控失败：由公开函数转成 `ToolResult(ok=False)`，绝不冒泡给编排层（卡 05–07 也用它）。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def set_current_user(user_id: str | None) -> None:
    """编排层（卡 09/10）在每个会话开始时设置当前用户；传 `None` 回到 demo 默认用户。"""
    global _SESSION_USER
    _SESSION_USER = user_id


def current_user_id() -> str:
    """当前会话用户 id：未显式设置时取合成数据的唯一用户（`data.seed.USER_ID`）。"""
    return _SESSION_USER or DEMO_USER_ID


def require_owned(resource: str, owner_id: str | None, resource_id: str) -> None:
    """L2 归属断言（规格第 6 节）：资源不属于当前用户 → `FORBIDDEN`。

    卡 06/07 的 get_card / get_subscription / get_product 等资源查询必须调用本函数。
    """
    if owner_id != current_user_id():
        logger.warning("越权访问被拦：resource=%s id=%s owner=%s user=%s",
                       resource, resource_id, owner_id, current_user_id())
        raise ToolError(ErrorCode.FORBIDDEN, f"{resource}不属于当前用户")


def _fail(code: ErrorCode, message: str) -> ToolResult:
    return ToolResult(ok=False, data=None, error_code=code, message=message, facts={})


def _ok(data: dict, facts: dict, message: str) -> ToolResult:
    return ToolResult(ok=True, data=data, error_code=None, message=message, facts=facts)


def _invalid(model: type[BaseModel], **kwargs: object) -> ToolResult | None:
    """入参模型严格校验；不合法 → INVALID_ARGUMENT（消息不含数字，明细进 logging）。"""
    try:
        model(**kwargs)
    except ValidationError as exc:
        logger.warning("参数不合法：%s", exc.errors())
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "参数"
        return _fail(ErrorCode.INVALID_ARGUMENT, f"参数不合法：{field}")
    return None


def _dao_reject(exc: ValueError) -> ToolResult:
    """DAO 的参数校验失败 → INVALID_ARGUMENT（DAO 的消息含数字，只写日志不给用户）。"""
    logger.warning("DAO 拒绝参数：%s", exc)
    return _fail(ErrorCode.INVALID_ARGUMENT, "参数不合法")


def _money(cents: int) -> str:
    """分 → 元字符串（纯整数运算，禁用浮点）：`-123456` → `-1,234.56`。"""
    whole, frac = divmod(abs(cents), CENTS_PER_YUAN)
    return f"{'-' if cents < 0 else ''}{whole:,}.{frac:02d}"


def _money_facts(cents: int, name: str) -> dict:
    """金额进事实包：整数分本体 + 展示形态（供数字校验器比对“元”写法）。"""
    return {name: cents, f"{name}_yuan": _money(cents)}


def _owned_account_ids() -> set[str]:
    """当前用户拥有的账户 id 集合（归属过滤；DAO 不按 user 圈定 → 这里 fail-closed）。

    已知后果：若同名类型下有他人的账户且 id 更小，DAO 会优先返回他人的行，本用户该类型账户
    即取不到 → 结果收窄为“只能确认归属的账户”（宁少勿漏），此风险已在账本台账登记。
    """
    owned = set()
    for kind in ACCOUNT_TYPES:
        row = dao.get_balance(kind)
        if row is not None and row["user_id"] == current_user_id():
            owned.add(row["id"])
    return owned
