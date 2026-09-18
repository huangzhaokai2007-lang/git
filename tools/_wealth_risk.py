"""工具层 T13–T14（任务卡 07）：风险评估 + 理财推荐；T15 在同族的 `tools/wealth.py`。

依据 `docs/01-接口规格.md` §2 T13–T14 + 卡 07 第 1–2 条：
- T13 `assess_risk`：**纯代码**按问卷计分得 R1–R5（规则表见下方常量），LLM 不参与判定；
  未成年人（<18）直接拒（卡 07「未成年人边界」）。
- T14 `recommend_wealth`：**只返回 risk_level ≤ 用户等级**的产品，按收益降序；只推荐不申购（规格 T14）。

为什么是私有子模块：卡 07 的 `tools/wealth.py` 单文件超 300 行上限（CLAUDE.md 第 51 行），
按本仓既有结构（`_query_analysis.py` / `_transfer_risk.py`）拆出；`tools/wealth.py` 继续 re-export
`assess_risk` / `recommend_wealth`，**调用方 import 路径不变**（规格冻结的是函数名，不是文件）。

口径（规格未定义处，逐条进交付说明的「需要人类决定」）：
- 问卷 5 题的题干/选项分值/R1–R5 分段/有效期：规格未定义 → 按卡 07 第 1 条「规则表写在代码注释里」自行定义
  （见 `QUESTION_SCORE_RULE` / `AGE_BANDS` / `LEVEL_BANDS`），**有效期取 365 天**。
- **用户风险等级存哪**：DDL 无存储表、规格 T15 签名也没有 risk 参数 → 本层用**进程内私有存储**
  `_ASSESSMENTS`（与 card-06 的 `confirm_ref`、card-05 的 `preview_token` 同族工具层私有约定，非冻结契约）；
  T15 据此校验「风险等级匹配」。持久化需补表 + DAO 原语 → `TODO(07b)`。
- **时间锚 = `data.seed.AS_OF`**（数据集的"今天"，固定常量 → 跨天可复现，与 seed 历史数据同一条时间线）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from data import dao
from data.seed import AS_OF
from tools._query_common import (
    ToolError, _dao_reject, _fail, _invalid, _money_facts, _ok, current_user_id,
)
from tools.schemas import ErrorCode, ToolResult
from tools.subscription import reject_audit

logger = logging.getLogger(__name__)

# ---------------- 阈值常量（唯一允许出现业务数字字面量的地方，逐条注明来源） ----------------
RISK_LEVELS = ("R1", "R2", "R3", "R4", "R5")     # 来源：DDL `wealth_product.risk_level` + 规格 T13/T14
RISK_VALID_DAYS = 365      # 来源：卡 07 第 1 条要求返回 valid_until；有效期长度规格未定 → demo 取 365 天
ADULT_AGE = 18             # 来源：卡 07「未成年人边界」= 未满 18 岁不得测评/买理财

#: 【计分规则表】来源：卡 07 第 1 条（5 题问卷 → 代码计分得 R1–R5，LLM 不参与）。
#: 4 道选择题按**选项下标 0..4** 计 1..5 分；年龄题（`age`，直接填岁数）按 `AGE_BANDS` 分段计 1..5 分。
QUESTION_SCORE_RULE = {
    "experience": (1, 2, 3, 4, 5),      # 投资经验：无 / <1年 / 1-3年 / 3-5年 / >5年
    "income": (1, 2, 3, 4, 5),          # 收入稳定性：很不稳定 / 一般 / 稳定 / 很稳定 / 极稳定
    "drawdown": (1, 2, 3, 4, 5),        # 可承受最大回撤：不接受 / ≤5% / ≤10% / ≤20% / >20%
    "horizon": (1, 2, 3, 4, 5),         # 投资期限：≤3个月 / 半年 / 1年 / 3年 / >5年
}
#: 年龄分段得分（下限闭、上限开）：18-25 → 1，26-40 → 2，41-55 → 3，56-65 → 4，>65 → 5
AGE_BANDS = ((18, 26, 1), (26, 41, 2), (41, 56, 3), (56, 66, 4), (66, 200, 5))
#: 总分（5–25）→ 风险等级分段
LEVEL_BANDS = ((5, 10, "R1"), (10, 14, "R2"), (14, 18, "R3"), (18, 22, "R4"), (22, 26, "R5"))
MAX_SCORE = 5 * (len(QUESTION_SCORE_RULE) + 1)    # 5 题 × 每题最高 5 分 = 25（供 facts 展示）

STRICT = ConfigDict(strict=True, extra="forbid")
#: 进程内私有存储：user_id → 测评结果（见文件头「用户风险等级存哪」）
_ASSESSMENTS: dict[str, dict] = {}


# ---------------- 出入参模型（卡 07 范围不扩 schemas.py → 模型放本族文件内，07b 归位） ----------------

class AssessRiskReq(BaseModel):
    model_config = STRICT

    answers: dict[str, int]


class AssessRiskData(BaseModel):
    risk_level: str
    valid_until: str


class RecommendWealthReq(BaseModel):
    model_config = STRICT

    risk_level: str | None = None
    horizon_days: int | None = Field(default=None, ge=0)
    amount: int | None = Field(default=None, ge=0)


class RecommendItem(BaseModel):
    """规格 T14 的 item 字段（冻结为 5 个，不得增删）。

    `yield` 取**百分数字符串**（如 `"3.20%"`），不取 DDL 的 `REAL`：项目红线「金额一律整数分、禁浮点」，
    把 REAL 原样透传会把浮点带进 facts/data（card-05 的 `fee` 恒 0 也是躲这个坑）。格式化保留 2 位小数，
    与 seed 注释「3.20 = 3.20%」一致，不引入新精度。
    """

    product_id: str
    name: str
    risk_level: str
    yield_: str = Field(alias="yield")
    term: int

    model_config = ConfigDict(populate_by_name=True)


# ---------------- 内部辅助 ----------------

def _rank(level: str) -> int:
    """风险等级 → 序数（R1=1..R5=5）；非法等级抛 `ToolError(INVALID_ARGUMENT)`。"""
    if level not in RISK_LEVELS:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"风险等级只能是 {'/'.join(RISK_LEVELS)}")
    return RISK_LEVELS.index(level) + 1


def _yield_text(value: float) -> str:
    """预期年化 → 百分数字符串（2 位小数，如 `3.20%`）；不引入浮点到 data/facts。"""
    return f"{value:.2f}%"


def _score(answers: dict[str, int]) -> int:
    """按 `QUESTION_SCORE_RULE` / `AGE_BANDS` 计分；缺题、越界、非整数一律拒。"""
    if set(answers) != set(QUESTION_SCORE_RULE) | {"age"}:
        raise ToolError(ErrorCode.INVALID_ARGUMENT,
                        f"问卷必须恰好包含 {'、'.join(sorted(set(QUESTION_SCORE_RULE) | {'age'}))} 五题")
    total = 0
    for key, rule in QUESTION_SCORE_RULE.items():
        value = answers[key]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(rule):
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{key} 的取值必须是 0..{len(rule) - 1} 的整数")
        total += rule[value]
    age = answers["age"]
    if not isinstance(age, int) or isinstance(age, bool) or age <= 0:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "age 必须是正整数岁数")
    if age < ADULT_AGE:
        raise ToolError(ErrorCode.FORBIDDEN, "未成年人不能做风险测评，也无法购买理财产品")
    total += next(score for low, high, score in AGE_BANDS if low <= age < high)
    return total


def require_assessment() -> dict:
    """取当前用户**有效**的测评结果（过期按未测评处理）。缺失 → `INVALID_STATE`（未测评边界）。"""
    record = _ASSESSMENTS.get(current_user_id())
    if record is None:
        raise ToolError(ErrorCode.INVALID_STATE, "尚未完成风险评估，请先做风险测评")
    if record["valid_until"] < AS_OF.isoformat():
        raise ToolError(ErrorCode.INVALID_STATE, "风险评估已过期，请重新测评")
    return record


# ---------------- T13 assess_risk ----------------

def assess_risk(answers: dict) -> ToolResult:
    """T13 风险评估（L0）。data: `risk_level`(R1–R5) / `valid_until`。

    纯代码计分（规则表见 `QUESTION_SCORE_RULE` / `AGE_BANDS` / `LEVEL_BANDS`），结果写进程内私有存储
    供 T14/T15 复用；**不落库、不写审计**（L0 只读，且库内没有测评结果表）。未成年人 → `FORBIDDEN` + 留痕。
    """
    if (bad := _invalid(AssessRiskReq, answers=answers)) is not None:
        return bad
    try:
        total = _score(answers)
    except ToolError as exc:
        if exc.code == ErrorCode.FORBIDDEN:              # 未成年人：留痕（reviewer 口径）
            reject_audit("assess_risk", "risk_assess", current_user_id(), exc.message, "L0")
        return _fail(exc.code, exc.message)
    level = next(band for low, high, band in LEVEL_BANDS if low <= total < high)
    valid_until = (AS_OF + timedelta(days=RISK_VALID_DAYS)).isoformat()
    _ASSESSMENTS[current_user_id()] = {"risk_level": level, "valid_until": valid_until}
    data = AssessRiskData(risk_level=level, valid_until=valid_until)
    facts = {"risk_level": level, "valid_until": valid_until, "total_score": total,
             "max_score": MAX_SCORE, "valid_days": RISK_VALID_DAYS, "answers": dict(answers)}
    message = f"根据问卷计分（{total} 分），您的风险承受等级为 {level}，有效期至 {valid_until}。"
    return _ok(data.model_dump(), facts, message)


# ---------------- T14 recommend_wealth ----------------

def recommend_wealth(risk_level: str | None = None, horizon_days: int | None = None,
                     amount: int | None = None) -> ToolResult:
    """T14 理财推荐（L0）。data: `items[{product_id,name,risk_level,yield,term}]`（字段冻结）。

    只推荐**不超过**用户风险等级的产品；`horizon_days` 过滤期限、`amount` 过滤起购额（给了才过滤）。
    `risk_level` 缺省时取本会话的测评结果（未测评 → `INVALID_STATE`）。只推荐，不申购。
    注：`yield` 是**费率**（DDL `expected_yield REAL`，非金额）原样透传；金额一律整数分。
    """
    if (bad := _invalid(RecommendWealthReq, risk_level=risk_level, horizon_days=horizon_days,
                        amount=amount)) is not None:
        return bad
    try:
        level = risk_level if risk_level is not None else require_assessment()["risk_level"]
        ceiling = _rank(level)
        products = [row for row in dao.list_products()
                    if _rank(row["risk_level"]) <= ceiling
                    and (horizon_days is None or row["term_days"] <= horizon_days)
                    and (amount is None or row["min_amount"] <= amount)]
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except ValueError as exc:
        return _dao_reject(exc)
    products.sort(key=lambda row: (-row["expected_yield"], row["min_amount"], row["id"]))
    items = [RecommendItem(product_id=row["id"], name=row["name"], risk_level=row["risk_level"],
                           yield_=_yield_text(row["expected_yield"]), term=row["term_days"])
             for row in products]
    data = {"items": [item.model_dump(by_alias=True) for item in items]}
    facts = {"risk_level": level, "ceiling_rank": ceiling, "item_count": len(items),
             "horizon_days": horizon_days, "amount": amount,
             "items": [{**item.model_dump(by_alias=True),
                        **_money_facts(next(row["min_amount"] for row in products
                                            if row["id"] == item.product_id), "min_amount")}
                       for item in items]}
    if not items:
        return _ok(data, facts, "按您的风险等级筛选后没有合适的产品。")
    message = f"按风险等级 {level} 筛出 {len(items)} 个产品，按预期年化从高到低推荐。"
    return _ok(data, facts, message)
