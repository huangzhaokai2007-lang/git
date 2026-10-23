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
from agent._write_aa_ops import (
    _card as _card_aa_op,
    _execute_cancel as _execute_aa_cancel,
    _execute_mark_paid as _execute_aa_mark_paid,
    _plain as _plain_aa_op,
    _prepare_cancel as _prepare_aa_cancel,
    _prepare_mark_paid as _prepare_aa_mark_paid,
    _shape_cancel as _shape_aa_cancel,
    _shape_mark_paid as _shape_aa_mark_paid,
)
from agent._write_gift_ops import (
    _card as _card_gift_order,
    _execute_cancel as _execute_gift_cancel,
    _execute_pay as _execute_gift_pay,
    _plain as _plain_gift_order,
    _prepare_cancel as _prepare_gift_cancel,
    _prepare_pay as _prepare_gift_pay,
    _shape as _shape_gift_order,
)
from agent._write_schedule_ops import (
    _card_cancel,
    _card_modify,
    _card_run,
    _execute_cancel,
    _execute_modify,
    _execute_run,
    _plain_cancel,
    _plain_modify,
    _plain_run,
    _prepare_cancel,
    _prepare_modify,
    _prepare_run,
    _shape_cancel,
    _shape_modify,
    _shape_run,
)
from agent._write_payee import (
    _card_blacklist,
    _card_delete,
    _card_unblacklist,
    _card_whitelist,
    _execute_blacklist,
    _execute_delete,
    _execute_unblacklist,
    _execute_whitelist,
    _plain_blacklist,
    _plain_delete,
    _plain_unblacklist,
    _plain_whitelist,
    _prepare_blacklist,
    _prepare_delete,
    _prepare_unblacklist,
    _prepare_whitelist,
    _shape_blacklist,
    _shape_delete,
    _shape_unblacklist,
    _shape_whitelist,
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
    _book_scheduled,
    _card_scheduled,
    _card_transfer,
    _execute_transfer,
    _plain_scheduled,
    _plain_transfer,
    _prepare_scheduled,
    _prepare_transfer,
    _shape_scheduled,
    _shape_transfer,
)
from agent._write_wealth import specs as _wealth_specs          # F12/F13（T15，confirm_ref 族）
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
    # F16：定时转账**已接通**（止损已移除）。与即时转账只差两处 —— PRECHECK 把 `schedule`
    # 一起传给 T7、EXECUTE 调 **T21 `schedule_transfer`** 落 `pending` 预约（**不扣款**）。
    # 到点执行由 `agent/scheduled_runner.py` 的惰性结算调 T22 完成。
    "transfer_scheduled": WriteSpec(
        intent="transfer_scheduled", intent_cn="定时转账", title="定时转账确认卡",
        credential_kind="preview_token", otp_in_tool=True,
        missing=("payee", "amount", "schedule"), tools=("preview_transfer",),
        execute_tool="schedule_transfer",
        target_key="payee_id",
        what="预约转账", shape=_shape_scheduled, prepare=_prepare_scheduled,
        card=_card_scheduled, plain=_plain_scheduled, execute=_book_scheduled),
    # F16 · Q4：取消预约 —— **恒 L1、不要 OTP**（撤回尚未发生的动作，风险低于发起、且不动钱）。
    # `shape`/`prepare` 只读 T24 `list_scheduled`，真取消在 EXECUTE 调 T23。
    "scheduled_cancel": WriteSpec(
        intent="scheduled_cancel", intent_cn="取消预约", title="取消预约确认卡",
        credential_kind="none", otp_in_tool=False,
        missing=(), tools=("list_scheduled",), execute_tool="cancel_scheduled",
        target_key="schedule_id",
        what="取消预约", shape=_shape_cancel, prepare=_prepare_cancel,
        card=_card_cancel, plain=_plain_cancel, execute=_execute_cancel),
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

#: F12/F13 理财申购赎回（按意图族拆出，同 `_write_subscription` 先例）
_SPECS.update(_wealth_specs())

#: F22b（T25）：把收款人加入白名单 —— **L2 + OTP**。理由：白名单 = 转账免 OTP 的特权，
#: 授予它必须单独验身份，否则账户被盗后可把自己加进白名单、永久绕过验证码。
#: 与 T11（订阅取消）同为 `confirm_ref` 族，链路同构。
_SPECS["payee_whitelist"] = WriteSpec(
    intent="payee_whitelist", intent_cn="加入白名单", title="白名单确认卡",
    credential_kind="confirm_ref", otp_in_tool=False,
    missing=("payee",), tools=("list_payees",), execute_tool="set_payee_whitelist",
    target_key="payee_id",
    what="加入白名单", shape=_shape_whitelist, prepare=_prepare_whitelist,
    card=_card_whitelist, plain=_plain_whitelist, execute=_execute_whitelist)

#: F22c（T28）：把收款人加入黑名单 —— **L1、无 OTP**。加黑是「阻断」不是「授权」，
#: 风险低于加白；人类需求只要求「解除黑名单」要验证码（后续卡做）。
_SPECS["payee_blacklist"] = WriteSpec(
    intent="payee_blacklist", intent_cn="加入黑名单", title="黑名单确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("payee",), tools=("list_payees",), execute_tool="set_payee_blacklist",
    target_key="payee_id",
    what="加入黑名单", shape=_shape_blacklist, prepare=_prepare_blacklist,
    card=_card_blacklist, plain=_plain_blacklist, execute=_execute_blacklist)

#: F22d（T29）：解除黑名单 —— **L2 + OTP**（人类需求原文「解除黑名单必须通过验证码确认」）。
#: 与加黑（L1）不对称是刻意的：加黑是阻断、解除是恢复，恢复需要更严的验证。
_SPECS["payee_unblacklist"] = WriteSpec(
    intent="payee_unblacklist", intent_cn="解除黑名单", title="解除黑名单确认卡",
    credential_kind="confirm_ref", otp_in_tool=False,
    missing=("payee",), tools=("list_payees",), execute_tool="unset_payee_blacklist",
    target_key="payee_id",
    what="解除黑名单", shape=_shape_unblacklist, prepare=_prepare_unblacklist,
    card=_card_unblacklist, plain=_plain_unblacklist, execute=_execute_unblacklist)

#: F22e（T30）：删除收款人 —— **L1、无 OTP**。删的是联系人、不动钱（与加黑同档）。
_SPECS["payee_delete"] = WriteSpec(
    intent="payee_delete", intent_cn="删除收款人", title="删除收款人确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("payee",), tools=("list_payees",), execute_tool="delete_payee",
    target_key="payee_id",
    what="删除收款人", shape=_shape_delete, prepare=_prepare_delete,
    card=_card_delete, plain=_plain_delete, execute=_execute_delete)

#: F16 追加（T32）：**立即执行**一条待执行预约。档位**沿用预约冻结值**（L1 免码 / L2 要码），
#: 扣款由 T22 的硬约束重检 —— 不重新定档（Q5 同理）。
_SPECS["scheduled_run"] = WriteSpec(
    intent="scheduled_run", intent_cn="立即执行预约", title="立即执行预约确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("schedule_id",), tools=("list_scheduled",), execute_tool="run_scheduled_now",
    target_key="schedule_id",
    what="立即执行预约", shape=_shape_run, prepare=_prepare_run,
    card=_card_run, plain=_plain_run, execute=_execute_run)

#: F16 追加（T33）：**改预约时间** —— L1（只改未来某一笔的时间，不动钱）。
_SPECS["scheduled_modify"] = WriteSpec(
    intent="scheduled_modify", intent_cn="修改预约时间", title="修改预约时间确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("schedule_id", "schedule"), tools=("list_scheduled",),
    execute_tool="reschedule_scheduled", target_key="schedule_id",
    what="修改预约时间", shape=_shape_modify, prepare=_prepare_modify,
    card=_card_modify, plain=_plain_modify, execute=_execute_modify)

#: F20 追加（T39）：**取消送礼订单** —— L1（只解锁冻结金额，余额不动）。
_SPECS["gift_order_cancel"] = WriteSpec(
    intent="gift_order_cancel", intent_cn="取消订单", title="取消订单确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("order_id",), tools=("list_gift_orders",), execute_tool="cancel_gift_order",
    target_key="order_id",
    what="取消订单", shape=_shape_gift_order, prepare=_prepare_gift_cancel,
    card=_card_gift_order, plain=_plain_gift_order, execute=_execute_gift_cancel)

#: F20 追加（T40）：**支付送礼订单** —— L2（真扣款，要验证码）。
_SPECS["gift_order_pay"] = WriteSpec(
    intent="gift_order_pay", intent_cn="支付订单", title="支付订单确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("order_id",), tools=("list_gift_orders",), execute_tool="pay_gift_order",
    target_key="order_id",
    what="支付订单", shape=_shape_gift_order, prepare=_prepare_gift_pay,
    card=_card_gift_order, plain=_plain_gift_order, execute=_execute_gift_pay)

#: F22 追加（T43）：**登记某人已付** —— L1（只改 AA 的份额状态，不动钱）。
_SPECS["aa_mark_paid"] = WriteSpec(
    intent="aa_mark_paid", intent_cn="登记已付", title="登记已付确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("payee",), tools=("list_aa_requests",), execute_tool="mark_aa_share_paid",
    target_key="request_id",
    what="登记已付", shape=_shape_aa_mark_paid, prepare=_prepare_aa_mark_paid,
    card=_card_aa_op, plain=_plain_aa_op, execute=_execute_aa_mark_paid)

#: F22 追加（T44）：**撤销 AA** —— L1（已登记的付款记录保留）。
_SPECS["aa_cancel"] = WriteSpec(
    intent="aa_cancel", intent_cn="撤销 AA", title="撤销 AA 确认卡",
    credential_kind="none", otp_in_tool=False,
    missing=("request_id",), tools=("list_aa_requests",), execute_tool="cancel_aa_request",
    target_key="request_id",
    what="撤销 AA", shape=_shape_aa_cancel, prepare=_prepare_aa_cancel,
    card=_card_aa_op, plain=_plain_aa_op, execute=_execute_aa_cancel)

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
