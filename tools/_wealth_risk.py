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

import hashlib
import logging
from datetime import date, timedelta

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
#: 等级的**中文标签**：对外展示一律用它，别把「R3」直接甩给用户（用户看不懂 R 几代表什么）。
#: 措辞对齐券商开户问卷的通俗分档（低/中低/中/中高/高），不照抄任何一家的原文。
RISK_LABELS = {"R1": "低风险", "R2": "中低风险", "R3": "中风险",
               "R4": "中高风险", "R5": "高风险"}
#: 等级的**风险说明**（确认卡上给用户看的一句话）。数字是「该等级的典型波动区间」，
#: 不是对某只产品的收益承诺 —— 它随 facts 一起下发，卡面数字才过得了校验器（铁律 2）。
RISK_DESC = {
    "R1": "本金风险很低，收益小幅波动，通常不亏本金",
    "R2": "本金风险较低，短期可能出现 5% 以内的波动",
    "R3": "本金有一定风险，可能出现 5%-10% 的波动",
    "R4": "本金风险较高，可能出现 10%-20% 的波动",
    "R5": "本金风险高，可能出现 20% 以上的波动，可能损失大部分本金",
}
#: 【模拟参考波动】按风险等级设置的演示用区间假设（百分比，正数）。
#: 来源：本 demo 的 RISK_DESC 文字分档（R2「5% 以内」、R3「5%-10%」、R4「10%-20%」、
#: R5「20% 以上」演示取 30%）。并非历史数据或可验证的最大收益/亏损，必须在界面显著标明。
RISK_SWING = {"R1": 0.0, "R2": 5.0, "R3": 10.0, "R4": 20.0, "R5": 30.0}
#: 历年表现的口径：最近五个完整年度（演示数据锚在 2026-09，故 2021–2025）。
HISTORY_YEARS = (2021, 2022, 2023, 2024, 2025)
#: 月 K 的口径：最近 60 个月（5 年，末月 = `AS_OF` 所在月），净值基点 10000 = 1.0000。
CANDLE_MONTHS = 60
_NAV_BASE = 10_000
#: 单月波动的分母：见 `_product_candles()` 里的注释（不随窗口长度变）。
_STEP_DIVISOR = 24
#: 日 K 默认窗口：近 120 个交易日（约半年）。
DAILY_DAYS = 120

RISK_VALID_DAYS = 365      # 来源：卡 07 第 1 条要求返回 valid_until；有效期长度规格未定 → demo 取 365 天
ADULT_AGE = 18             # 来源：卡 07「未成年人边界」= 未满 18 岁不得测评/买理财

#: 【计分规则表】来源：卡 07 第 1 条（问卷 → 代码计分得 R1–R5，LLM 不参与）。
#: 8 道选择题按**选项下标 0..4** 计 1..5 分；年龄题（`age`，直接填岁数）按 `AGE_BANDS` 分段计 1..5 分。
#: 问卷维度参考券商开户（经验/收入/年收入/资金占比/亏损承受/期限/目标/亏损反应），题面在
#: `agent/_risk_questions.py`，两处**必须同序**（下标 i → i+1 分）。
QUESTION_SCORE_RULE = {
    "experience": (1, 2, 3, 4, 5),      # 投资经验：无 / <1年 / 1-3年 / 3-5年 / >5年
    "income": (1, 2, 3, 4, 5),          # 收入稳定性：很不稳定 / 不太稳定 / 一般 / 比较稳定 / 非常稳定
    "income_level": (1, 2, 3, 4, 5),    # 家庭年收入：10万以下 / 10-30万 / 30-50万 / 50-100万 / 100万以上
    "asset_ratio": (1, 2, 3, 4, 5),     # 投资资金占可投资资产：10%以下 / 10-30% / 30-50% / 50-80% / 80%以上
    "drawdown": (1, 2, 3, 4, 5),        # 可承受最大回撤：不接受 / ≤5% / 5-10% / 10-20% / >20%
    "horizon": (1, 2, 3, 4, 5),         # 投资期限：≤3月 / 3-12月 / 1-3年 / 3-5年 / >5年
    "goal": (1, 2, 3, 4, 5),            # 投资目标：保本 / 稳健增值 / 平衡 / 较高收益 / 高收益
    "reaction": (1, 2, 3, 4, 5),        # 亏损反应：全部卖出 / 卖一部分 / 持有观望 / 补仓 / 大幅加仓
}
#: 年龄分段得分（下限闭、上限开）：18-25 → 1，26-40 → 2，41-55 → 3，56-65 → 4，>65 → 5
AGE_BANDS = ((18, 26, 1), (26, 41, 2), (41, 56, 3), (56, 66, 4), (66, 200, 5))
#: 总分（9–45）→ 风险等级分段（每档 8 分，末档到 45）
LEVEL_BANDS = ((9, 17, "R1"), (17, 25, "R2"), (25, 33, "R3"), (33, 41, "R4"), (41, 46, "R5"))
MAX_SCORE = 5 * (len(QUESTION_SCORE_RULE) + 1)    # 9 题 × 每题最高 5 分 = 45（供 facts 展示）
MIN_SCORE = 1 * (len(QUESTION_SCORE_RULE) + 1)    # 全选最低档 = 9 分（供 facts 展示「分数区间」）

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

#: 中文等级 → 代码。**LLM 抽槽位时经常直接给中文**（"我是高风险"），
#: 只认 R1–R5 会让整个推荐报 INVALID_ARGUMENT，用户看到的是一句错误而不是产品列表。
_LABEL_TO_CODE = {label: code for code, label in RISK_LABELS.items()}


def normalize_level(level: str) -> str:
    """把「高风险」/「r5」/「R5」统一成 `R5`；认不出就原样返回（交给 `_rank` 报错）。"""
    text = str(level).strip()
    if text.upper() in RISK_LEVELS:
        return text.upper()
    if text.isdigit() and 1 <= int(text) <= 5:      # 「5」→ R5
        return f"R{text}"
    return _LABEL_TO_CODE.get(text, text)


def _resolve_level(risk_level: str | None) -> str:
    """定本次推荐的等级上限。

    **严格与容错的分界**：
    - 没传 → 用本次会话的测评结果
    - `R5` / `5` / `高风险` → 用用户指定的
    - `R9` / `R0` / `r1` / 空串 → **报 `INVALID_ARGUMENT`**（明显的输入错误，必须让用户知道）
    - `high` / `较高` / 其他认不出的人话 → **回退测评结果**。
      LLM 抽槽位时经常给 `high` 这种英文词，为它把整个推荐打成报错不值得 ——
      用户要的是产品列表，不是一句「风险等级只能是 R1/R2/...」。
    """
    #: 已有测评时，分类器不能用更高等级槽位放宽推荐；显式等级但没有测评时
    #: 保留 T14 的历史查询契约（仅用于浏览产品，买入仍强制要求真实测评）。
    if risk_level is None:
        return require_assessment()['risk_level']
    record = _ASSESSMENTS.get(current_user_id())
    assessed = require_assessment()['risk_level'] if record is not None else None
    text = str(risk_level).strip()
    if not text:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "风险等级不能是空字符串")
    if text[0].upper() == "R" or text.isdigit():          # 代码式输入：严格校验
        level = f"R{text}" if text.isdigit() else text
        if level not in RISK_LEVELS:
            raise ToolError(ErrorCode.INVALID_ARGUMENT,
                            f"风险等级只能是 {'/'.join(RISK_LEVELS)}")
        return level if assessed is None or _rank(level) <= _rank(assessed) else assessed
    if text in _LABEL_TO_CODE:                            # 中文等级：认
        level = _LABEL_TO_CODE[text]
        return level if assessed is None or _rank(level) <= _rank(assessed) else assessed
    logger.warning("认不出的风险等级 %r，回退到测评结果", risk_level)
    return require_assessment()['risk_level']


def _rank(level: str) -> int:
    """风险等级 → 序数（R1=1..R5=5）；非法等级抛 `ToolError(INVALID_ARGUMENT)`。"""
    level = normalize_level(level)
    if level not in RISK_LEVELS:
        raise ToolError(ErrorCode.INVALID_ARGUMENT,
                        f"风险等级只能是 {'/'.join(RISK_LEVELS)} 或对应的中文名")
    return RISK_LEVELS.index(level) + 1


def _pct_facts(basis_points: int, name: str) -> dict:
    """百分比进事实包：**整数基点**本体 + 展示形态（红线 2：facts 里不许出现 float）。

    零值不带符号（`0.00%`），否则货基「年内最大回撤」会显示成 `+0.00%`，读着像涨了。
    """
    text = "0.00%" if basis_points == 0 else f"{basis_points / 100:+.2f}%"
    return {name: basis_points, f"{name}_text": text}


def _product_history(product_id: str, max_gain: float, max_loss: float) -> list[dict]:
    """三年模拟表现：**同一个产品 id 永远得到同一组数字**（sha256 取种子，跨机器可复现）。

    采样区间**就是卡片上那条区间**（`[max_loss, max_gain]`）—— 否则会出现
    「卡片写 0.00~+2.10%，展开却看到 +2.31%」这种自相矛盾（实测踩过）。
    回撤幅度同样不超过下沿。

    为什么是模拟：演示数据集没有净值表，规格也未定义历史业绩。界面必须标明「模拟」。
    将来若接真实净值，只换本函数实现，`facts.items[].history` 的形状不变。
    """
    span = max_gain - max_loss
    rows: list[dict] = []
    for year in HISTORY_YEARS:
        digest = hashlib.sha256(f"{product_id}:{year}".encode()).digest()
        r1 = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        r2 = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
        ret = max_loss + span * r1
        dd = -abs(max_loss) * (0.35 + 0.65 * r2)
        rows.append({"year": year,
                     **_pct_facts(round(ret * 100), "return"),
                     **_pct_facts(round(dd * 100), "drawdown")})
    return rows


def _candle_period(offset: int) -> str:
    """第 `offset` 个月（0 = 最早）的 `YYYY-MM`；末月 = `AS_OF` 所在月。"""
    end = AS_OF.year * 12 + (AS_OF.month - 1)
    total = end - (CANDLE_MONTHS - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _product_candles(product_id: str, max_gain: float, max_loss: float) -> list[dict]:
    """最近 `CANDLE_MONTHS` 个月的模拟月 K（净值，整数基点；`10000` = 1.0000）。

    同样按 `product_id + 月份` 取 sha256 种子 → 同一产品永远同一根 K 线。
    净值被夹在 `[1+max_loss%, 1+max_gain%]` 之间，和卡片上那条区间保持一致；
    单月波动按档位放大（R5 抖得比 R1 厉害），看起来才像真的净值曲线。
    """
    span = max(max_gain - max_loss, 1.0)
    #: 单月波动按**年化区间**定，不随窗口长度稀释：`span/24` 是「两年摊平」的直觉刻度。
    #: 若按 `span/CANDLE_MONTHS` 摊到 60 个月，实体只有 1.8px，图上几乎看不见。
    step = span / _STEP_DIVISOR * 0.9
    floor_nav = _NAV_BASE * (1 + max_loss / 100)
    ceil_nav = _NAV_BASE * (1 + max_gain / 100)
    #: 月漂移 = 年化收益 / 12（`yield = max_gain + max_loss`）。
    #: **必须压在区间内**：货基年化 2.1%、窗口 24 个月本可涨 4.2%，但上沿只有 +2.1%，
    #: 不封顶就会顶在天花板上一半的月份，画出来是条直线。取区间 60% 作上限。
    yield_pct = max_gain + max_loss
    drift = min(yield_pct / 12, max_gain * 0.6 / CANDLE_MONTHS) / 100
    nav = float(_NAV_BASE)
    dev = 0.0                                    # 上个月的「偏离趋势」部分，给动量用
    rows: list[dict] = []
    for offset in range(CANDLE_MONTHS):
        period = _candle_period(offset)
        digest = hashlib.sha256(f"{product_id}:k:{period}".encode()).digest()
        u1 = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        u2 = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
        #: **动量**：`dev = 0.4 * 上月偏离 + 0.8 * 本月冲击`。
        #: 纯独立随机会画成锯齿（相邻 K 线一半时间反向），看着不像行情；带自相关后
        #: 连续几个月朝一个方向走，才像一段趋势。实测转向率从 50% 降到 ~35%。
        #: 系数 0.55 会更顺，但净值更容易撞到区间边界贴着走，0.4 是折中。
        dev = 0.4 * dev + 0.8 * (u1 * 2 - 1) * step / 100
        open_nav = nav
        raw_nav = open_nav * (1 + drift + dev)
        close_nav = max(floor_nav, min(ceil_nav, raw_nav))
        if close_nav != raw_nav:
            #: 撞到区间边界就**反弹**：不处理的话，动量会让净值贴着上沿走十几个月的直线。
            dev = -0.6 * dev
        wick = abs(u2 - 0.5) * step / 100 * open_nav
        rows.append({"period": period,
                     "open": round(open_nav),
                     "high": round(max(open_nav, close_nav) + wick),
                     "low": round(min(open_nav, close_nav) - wick),
                     "close": round(close_nav)})
        nav = close_nav
    return rows


def _range_text(pct: float) -> str:
    """百分比数值 → 带符号的两位小数字符串（如 `+38.50` / `-30.00` / `0.00`）。

    用于「参考区间」：正数显式带 `+`，让用户一眼分清赚和亏。
    """
    return f"{pct:+.2f}" if pct else "0.00"


def _yield_text(value: float) -> str:
    """预期年化 → 百分数字符串（2 位小数，如 `3.20%`）；不引入浮点到 data/facts。"""
    return f"{value:.2f}%"


def risk_label(level: str) -> str:
    """风险等级的中文标签（「R3」→「中风险」）；未知等级原样回显，不猜。"""
    return RISK_LABELS.get(str(level), str(level))


def _score(answers: dict[str, int]) -> int:
    """按 `QUESTION_SCORE_RULE` / `AGE_BANDS` 计分；缺题、越界、非整数一律拒。"""
    if set(answers) != set(QUESTION_SCORE_RULE) | {"age"}:
        raise ToolError(ErrorCode.INVALID_ARGUMENT,
                        f"问卷必须恰好包含 {'、'.join(sorted(set(QUESTION_SCORE_RULE) | {'age'}))} "
                        f"{len(QUESTION_SCORE_RULE) + 1} 题")
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


def current_profile() -> dict:
    """当前用户的测评**状态**：`{"status": "ok" | "missing" | "expired", ...}`，不抛异常。

    与 `require_assessment()` 的分工：那个给"要买理财"的路径用（缺就 INVALID_STATE 拦住），
    这个给"我只是想看看自己是什么等级"用 —— 没测评不是错误，是要如实告诉用户。

    **为什么区分 missing / expired**：都回"您还没做过测评"是错的 —— 用户明明做过、只是过期了，
    这么说会让他以为自己白做了。过期时要把"什么时候到期"说出来。
    """
    record = _ASSESSMENTS.get(current_user_id())
    if record is None:
        return {"status": "missing"}
    if record["valid_until"] < AS_OF.isoformat():
        return {"status": "expired", "risk_level": record["risk_level"],
                "valid_until": record["valid_until"]}
    return {"status": "ok", "risk_level": record["risk_level"],
            "valid_until": record["valid_until"]}


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
    facts = {"risk_level": level, "risk_label": risk_label(level), "valid_until": valid_until,
             "total_score": total, "max_score": MAX_SCORE, "valid_days": RISK_VALID_DAYS,
             "answers": dict(answers)}
    message = (f"根据问卷计分（{total} 分），您的风险承受能力为「{risk_label(level)}」，"
               f"有效期至 {valid_until}。")
    return _ok(data.model_dump(), facts, message)


# ---------------- T14b product_daily（日 K，按需拉取） ----------------

def _trading_days(count: int) -> list[date]:
    """最近 `count` 个**交易日**（只跳周末；演示数据集没有节假日表），末个 = `AS_OF`。"""
    days: list[date] = []
    cursor = AS_OF
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _daily_candles(product_id: str, max_gain: float, max_loss: float,
                   days: int) -> list[list]:
    """近 `days` 个交易日的模拟日 K，紧凑数组 `[日期, 开, 高, 低, 收]`（整数基点）。

    **为什么用数组不用对象**：日 K 一个产品就 120 行，用 dict 每行多背 5 个键，
    14 只产品会多出 150 KB。数组形式省 45%，界面解析一行代码的事。

    日波动 = 月波动 / √21；动量系数取 0.85（日频要比月频更"粘"，否则仍是锯齿）。
    """
    span = max(max_gain - max_loss, 1.0)
    step = span / _STEP_DIVISOR * 0.9 / (21 ** 0.5)
    floor_nav = _NAV_BASE * (1 + max_loss / 100)
    ceil_nav = _NAV_BASE * (1 + max_gain / 100)
    yield_pct = max_gain + max_loss
    drift = min(yield_pct / 252, max_gain * 0.6 / days) / 100
    nav = float(_NAV_BASE)
    dev = 0.0
    rows: list[list] = []
    for day in _trading_days(days):
        period = day.isoformat()
        digest = hashlib.sha256(f"{product_id}:d:{period}".encode()).digest()
        u1 = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        u2 = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
        dev = 0.85 * dev + (1 - 0.85 ** 2) ** 0.5 * (u1 * 2 - 1) * step / 100
        open_nav = nav
        raw_nav = open_nav * (1 + drift + dev)
        close_nav = max(floor_nav, min(ceil_nav, raw_nav))
        if close_nav != raw_nav:
            dev = -0.6 * dev
        wick = abs(u2 - 0.5) * step / 100 * open_nav
        rows.append([period, round(open_nav),
                     round(max(open_nav, close_nav) + wick),
                     round(min(open_nav, close_nav) - wick),
                     round(close_nav)])
        nav = close_nav
    return rows


class DailyArgs(BaseModel):
    """T14b 入参：`days` 限定在 20–250 个交易日（约 1–12 个月）。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    days: int = Field(default=DAILY_DAYS, ge=20, le=250)


def product_daily(product_id: str, days: int = DAILY_DAYS) -> ToolResult:
    """T14b：单只产品的日 K（近 `days` 个交易日）。**L0 只读**、公共目录、无归属断言。

    为什么单独一个工具：日 K 一个产品就 120 行，14 只全塞进推荐 facts 会多出 150 KB。
    改成「点开哪只看哪只」，一次只取约 5 KB。
    """
    bad = _invalid(DailyArgs, days=days)
    if bad is not None:
        return bad
    try:
        products = dao.list_products()
    except ValueError as exc:
        return _dao_reject(exc)
    row = next((item for item in products if item["id"] == product_id), None)
    if row is None:
        return _fail(ErrorCode.NOT_FOUND, "没有这个理财产品")
    level = row["risk_level"]
    swing = RISK_SWING.get(level, 0.0)
    gain, loss = row["expected_yield"] + swing, -swing
    candles = _daily_candles(product_id, gain, loss, days)
    facts = {"product_id": product_id, "name": row["name"], "risk_level": level,
             "risk_label": risk_label(level), "day_count": len(candles),
             "candles": candles}
    data = {"product_id": product_id, "name": row["name"],
            "candles": [{"period": c[0], "open": c[1], "high": c[2],
                         "low": c[3], "close": c[4]} for c in candles]}
    return _ok(data, facts, f"{row['name']} 近 {len(candles)} 个交易日的日 K。")


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
        level = _resolve_level(risk_level)
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
    #: 可用余额（整数分）：界面据此把「起购额 > 余额」的订购按钮置灰。
    #: 归属 fail-closed —— 取到的账户不是本人就当没有余额（宁可少显示，也不误判为买得起）。
    #: **读余额失败不能让推荐整体失败**：取不到就当未知（不置灰），申购时还有一次余额校验兜底。
    try:
        account = dao.get_balance("savings")
    except ValueError:
        account = None
    available = (int(account["balance"])
                 if account is not None and account["user_id"] == current_user_id() else None)
    #: 演示区间：参考上沿 = 预期年化 + 演示波动值；下沿 = -演示波动值。
    #: 只进 facts（不进 data —— data 字段是冻结契约），渲染层据此展示。
    swings = {row["id"]: RISK_SWING.get(row["risk_level"], 0.0) for row in products}
    #: 每只产品的盈亏区间（上沿, 下沿）：卡片图例与历年表现**共用同一个区间**，保证不打架。
    bounds = {row["id"]: (row["expected_yield"] + swings[row["id"]], -swings[row["id"]])
              for row in products}
    data = {"items": [item.model_dump(by_alias=True) for item in items]}
    facts = {"risk_level": level, "risk_label": risk_label(level),
             "ceiling_rank": ceiling, "item_count": len(items),
             "horizon_days": horizon_days, "amount": amount,
             **(_money_facts(available, "available") if available is not None else {}),
             "items": [{**item.model_dump(by_alias=True),
                        "risk_label": risk_label(item.risk_level),
                        **_money_facts(next(row["min_amount"] for row in products
                                            if row["id"] == item.product_id), "min_amount"),
                        #: 历年表现（模拟）：区间与卡片图例**同源**，界面点开直接渲染，无需再调工具。
                        "history": _product_history(item.product_id, *bounds[item.product_id]),
                        #: 月 K（模拟）：只随推荐下发 —— 全部产品目录（T22）不带，避免 payload 翻几倍。
                        "candles": _product_candles(item.product_id, *bounds[item.product_id]),
                        #: 买得起吗 —— 由工具层判定（铁律 1：编排层与界面不做业务算术）。
                        "affordable": (available is None or
                                       next(row["min_amount"] for row in products
                                            if row["id"] == item.product_id) <= available),
                        "max_gain_pct": _range_text(bounds[item.product_id][0]),
                        "max_loss_pct": _range_text(bounds[item.product_id][1]),
                        #: 期限的月/年整数形态：渲染层会把 180 天说成「6 个月」、730 天说成「2 年」，
                        #: 那两个数字必须能在 facts 里找到，否则数字校验器判成幻觉。
                        "term_months": int(item.term) // 30,
                        "term_years": int(item.term) // 365}
                       for item in items],
             "portfolio_max_gain_pct": _range_text(
                 max((row["expected_yield"] + swings[row["id"]]) for row in products)
                 if products else 0.0),
             "portfolio_max_loss_pct": _range_text(-max(swings.values()) if swings else 0.0)}
    if not items:
        return _ok(data, facts, "按您的风险等级筛选后没有合适的产品。")
    message = f"按风险等级「{risk_label(level)}」筛出 {len(items)} 个产品，按预期年化从高到低推荐。"
    return _ok(data, facts, message)
