"""工具层数据模型（任务卡 04）：统一返回结构 `ToolResult` + T1–T5 的入参/出参模型。

依据 `docs/01-接口规格.md` 第 2 节（函数名与字段名冻结，不得增删）+ `CLAUDE.md` 铁律：
- 金额一律**整数分**（int），全程不出现 float；百分比也是整数（口径见 `tools/query.py`）。
- `facts` 事实包：本结果中**允许被回执引用**的数字集合，供 `guard/facts_check.py`（卡 13）比对。
  出参模型里的每个金额都同时给一份 `*_yuan` 展示字符串，供校验器比对"元"形态的数字。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 严格模式：不做类型强转（"50" 不当 50，True 不当 1），多余参数直接拒
STRICT = ConfigDict(strict=True, extra="forbid")

ACCOUNT_TYPES = ("savings", "credit")
MAX_LIMIT = 500   # 单次翻页上限；来源：data/dao.py 的 MAX_LIMIT（测试断言两者一致）


class ErrorCode(StrEnum):
    """规格第 2 节列出的 error_code（已入册：SPEC-CHANGE e4e847a/c05562c），
    与本枚举一一对应，不得增删。"""

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    OVER_LIMIT = "OVER_LIMIT"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    FORBIDDEN = "FORBIDDEN"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    TOO_MANY_ROWS = "TOO_MANY_ROWS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_STATE = "INVALID_STATE"
    HALLUCINATION_BLOCKED = "HALLUCINATION_BLOCKED"


class ToolResult(BaseModel):
    """规格第 2 节的统一返回外层结构（字段名冻结）。"""

    ok: bool
    data: dict | None = None
    error_code: str | None = None
    message: str = ""
    facts: dict = Field(default_factory=dict)


# ---------- T1 get_balance ----------

class GetBalanceReq(BaseModel):
    model_config = STRICT

    account_type: Literal["savings", "credit"]


class BalanceData(BaseModel):
    balance: int
    available: int
    as_of: str


# ---------- T2 list_txn ----------

class ListTxnReq(BaseModel):
    model_config = STRICT

    date_from: str
    date_to: str
    category: str | None = Field(default=None, min_length=1)
    min_amount: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=500)


class TxnItem(BaseModel):
    """流水行（字段与 DDL 一致；`memo` 是不可信文本，包裹由编排层负责 —— 铁律 7）。"""

    id: str
    account_id: str
    ts: str
    amount: int
    direction: str
    counterparty: str | None = None
    category: str | None = None
    channel: str | None = None
    memo: str | None = None
    balance_after: int


class ListTxnData(BaseModel):
    items: list[TxnItem]
    total_count: int


# ---------- T3 analyze_spending ----------

class AnalyzeSpendingReq(BaseModel):
    model_config = STRICT

    period: str
    group_by: Literal["category", "channel"] = "category"


class SpendingGroup(BaseModel):
    key: str
    amount: int
    pct: int


class SpendingData(BaseModel):
    groups: list[SpendingGroup]
    total: int
    vs_prev_pct: int | None = None


# ---------- T4 detect_anomalies ----------

class DetectAnomaliesReq(BaseModel):
    model_config = STRICT

    period: str


class AnomalyItem(BaseModel):
    txn_id: str
    reason: str                 # 规则名，**不含数字**（数字口径一律走 facts，避免幻觉校验误报）
    severity: Literal["low", "medium", "high"]


class AnomaliesData(BaseModel):
    items: list[AnomalyItem]


# ---------- T5 generate_bill_report ----------

class GenerateBillReportReq(BaseModel):
    model_config = STRICT

    period: str
    kind: Literal["monthly", "yearly"] = "monthly"


class BillReportData(BaseModel):
    markdown: str
    summary_numbers: dict[str, int]

# ---------- T6–T9 转账类（任务卡 05 定义，卡 05b 归位 schemas.py：CLAUDE.md 要求模型放这里） ----------

class ResolvePayeeReq(BaseModel):
    model_config = STRICT

    query: str = Field(min_length=1)

class PayeeCandidate(BaseModel):
    id: str
    name: str
    masked_phone: str | None = None

class PayeeData(BaseModel):
    candidates: list[PayeeCandidate]
    ambiguous: bool

class PreviewTransferReq(BaseModel):
    model_config = STRICT

    payee_id: str = Field(min_length=1)
    amount: int = Field(gt=0)
    schedule: str | None = None
    split_with: list[str] | None = None

class PreviewData(BaseModel):
    preview_token: str
    fee: int
    tier: Literal["L0", "L1", "L2", "L3"]
    requires_otp: bool
    limits: dict

class ExecuteTransferReq(BaseModel):
    model_config = STRICT

    preview_token: str = Field(min_length=1)
    otp: str | None = None

class ExecuteData(BaseModel):
    txn_id: str
    amount: int
    payee_name: str
    balance_after: int

class AaRequestReq(BaseModel):
    model_config = STRICT

    payee_ids: list[str] = Field(min_length=1)
    amount: int = Field(gt=0)

class AaData(BaseModel):
    request_id: str
    per_person_amount: int
