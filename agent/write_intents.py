"""写意图描述符表（card-24 从 `write_flow.py` 拆出；F15/F20 按意图族再拆）：
把「转账专属」的写路径泛化成「每个写意图声明自己的形状」，新增写功能只需加一条描述符。

**本模块现在只留「注册表 + 对外 API」**（F15/F20 拆卡）：

| 模块 | 内容 |
|---|---|
| `agent/_write_common.py` | 核心契约类型（`ShapeResult` / `Prepared` / `WriteSpec`）+ `_code_of` / `yuan_to_cents` |
| `agent/_write_transfer.py` | 转账族 T6/T7（`preview_token` 族，**OTP 在工具层**） |
| `agent/_write_subscription.py` | 订阅取消族 T11（`confirm_ref` 族，**OTP 在编排层**） |
| **本模块** | `_SPECS` 注册表 + `spec_of` / `WRITE_INTENTS` / `otp_ok`，并**再导出**核心类型与 `yuan_to_cents` |

**为什么拆**：本文件曾到 **296 行**，距 `CLAUDE.md`「单文件 ≤300 行」红线只剩 4 行，装不下 F15
（AA 收款）与 F20（送礼计划）的描述符。拆法照 card-04b / card-05b 的先例（按域拆 + 只许单向依赖）：
`_write_common` ← 各意图族 ← 本模块，无环。

**orchestrator 只 import 本模块**，其源码里不出现任何写工具名字面量
（`tests/test_orchestrator_readonly.py` 的静态红线）。

**两种凭证（card-24 台账口径，不得混用）**：

| 凭证 | 签发方 | 使用者 | OTP 校验位置 |
|---|---|---|---|
| `preview_token` | 工具层 `preview_*`（T7） | 转账 | **工具层** |
| `confirm_ref` | 工具层私有约定 `issue_confirm_ref`（T11/T12/T15） | 订阅取消 | **编排层** |

**新增写意图的步骤**：① 在 `_SPECS` 加一条 `WriteSpec`；② 在对应族的 `_write_*.py` 实现五个纯函数
（新族则新建 `agent/_write_<族>.py`）；③ 在 `tests/cases/orchestrator.yaml` 加用例。
**动到转账相关代码时，既有 trf-* 用例是硬判据。**
"""

from __future__ import annotations

from agent._write_common import (       # noqa: F401 —— 再导出，保住 `write_intents.<name>` 历史调用点
    Prepared,
    ShapeResult,
    WriteSpec,
    yuan_to_cents,
)
from agent._write_aa import (
    _card_aa,
    _execute_aa,
    _plain_aa,
    _prepare_aa,
    _shape_aa,
)
from agent._write_gift import (
    _card_gift,
    _execute_gift,
    _plain_gift,
    _prepare_gift,
    _shape_gift,
)
from agent._write_subscription import (
    CANCEL_ACTION,
    CANCEL_CN,
    _card_subscription,
    _execute_subscription,
    _plain_subscription,
    _prepare_subscription,
    _shape_subscription,
)
from agent._write_transfer import (
    _card_transfer,
    _execute_transfer,
    _plain_transfer,
    _prepare_transfer,
    _shape_transfer,
)
from tools.transfer import OTP_CODE     # agent→tools 合法；OTP 明文绝不进日志/facts/回执

# ---------------- 描述符表（唯一来源） ----------------

_SPECS: dict[str, WriteSpec] = {
    "transfer_single": WriteSpec(
        intent="transfer_single", intent_cn="单笔转账", title="转账确认卡",
        credential_kind="preview_token", otp_in_tool=True,
        missing=("payee", "amount"), tools=("preview_transfer",), execute_tool="execute_transfer",
        target_key="payee_id",
        what="转账", shape=_shape_transfer, prepare=_prepare_transfer,
        card=_card_transfer, plain=_plain_transfer, execute=_execute_transfer),
    "transfer_scheduled": WriteSpec(
        intent="transfer_scheduled", intent_cn="定时转账", title="转账确认卡",
        credential_kind="preview_token", otp_in_tool=True,
        missing=("payee", "amount"), tools=("preview_transfer",), execute_tool="execute_transfer",
        target_key="payee_id",
        what="转账", shape=_shape_transfer, prepare=_prepare_transfer,
        card=_card_transfer, plain=_plain_transfer, execute=_execute_transfer),
    "subscription_cancel": WriteSpec(
        intent="subscription_cancel", intent_cn=CANCEL_CN, title="订阅取消确认卡",
        credential_kind="confirm_ref", otp_in_tool=False,
        missing=("merchant",), tools=("list_subscriptions",), execute_tool=CANCEL_ACTION,
        target_key="sub_id",
        what="取消订阅", shape=_shape_subscription, prepare=_prepare_subscription,
        card=_card_subscription, plain=_plain_subscription, execute=_execute_subscription),
    # F15：**无凭证族**（T9 签名没有 preview_token / confirm_ref），L1 不要 OTP。
    # `otp_in_tool=False` 表示「编排层不管 OTP」；因 L1 的 `requires_otp=False`，
    # `write_flow._execute` 的闸门不会触发（闸门条件已加 `confirmation.requires_otp`）。
    "aa_collect": WriteSpec(
        intent="aa_collect", intent_cn="AA 收款", title="AA 收款确认卡",
        credential_kind="none", otp_in_tool=False,
        missing=("payee_ids", "amount"), tools=(),      # PRECHECK 阶段**不调任何工具**（T9 无 preview）
        execute_tool="create_aa_request",
        target_key="aa_target",
        what="AA 收款", shape=_shape_aa, prepare=_prepare_aa,
        card=_card_aa, plain=_plain_aa, execute=_execute_aa),
    # F20：**无凭证族**（T16 签名没有凭证参数），L2 要 OTP —— 闸门落在编排层（同 T11/T15）。
    # 确认卡数字取自 T19 `preview_gift`（L0 只读，SPEC-CHANGE 新增）：它一调不锁款，
    # 真锁款只在 `execute` 调 T16 `plan_gift` 时发生。
    "gift_plan": WriteSpec(
        intent="gift_plan", intent_cn="送礼计划", title="送礼计划确认卡",
        credential_kind="none", otp_in_tool=False,
        missing=("contact", "date", "budget"), tools=("preview_gift",),
        execute_tool="plan_gift",
        target_key="contact",
        what="送礼计划", shape=_shape_gift, prepare=_prepare_gift,
        card=_card_gift, plain=_plain_gift, execute=_execute_gift),
}

#: 已接通的写意图（`write_flow.WRITE_INTENTS` 引它 —— 单一来源，避免两份清单漂移）
WRITE_INTENTS: tuple[str, ...] = tuple(_SPECS)


def spec_of(intent: str) -> WriteSpec | None:
    """取意图的写描述符；未接通返回 None（调用方按「未接通」处理）。"""
    return _SPECS.get(intent)


def otp_ok(otp: str | None) -> bool:
    """编排层的 OTP 比对（**只给 `confirm_ref` 族用**；`preview_token` 族由工具层判）。

    常量引自 `tools.transfer.OTP_CODE`（规格 §5：demo 固定 `123456`），
    `tools/card.OTP_CODE` 与它同值（有测试钉着）；OTP 明文**绝不进日志/facts/回执**。
    """
    return str(otp or "").strip() == OTP_CODE
