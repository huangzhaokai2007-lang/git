"""F10（任务卡 07）：风险测评的**编排层**入口（问卷两段式，工具层是 L0 只读）。

为什么单开一个文件：`orchestrator.py` 283 行（红线 300，CLAUDE.md 第 51 行），而 F10 要加
「弹问卷」分支 + 表单提交入口 + 一句话解析；照 `agent/payee_flow.py`（卡 20）的先例整块挪出来，
`orchestrator` 按需 import。**本模块不是第二套写路径**：T13 是 **L0 只读**（规格 §2 表第 108 行
权限档列 = L0），不落库、不发确认卡、不要 OTP、不进 `write_flow`。

两条路径分开（与 `payee_flow` 同构）：
- 聊天触发：`orchestrator.handle()` 判出 `risk_assess` → 回一句引导语 + 让界面弹问卷（**不计分**）；
- 表单提交：`submit_assessment(answers, session_id=...)` → 一次把 5 题交给 `tools.assess_risk` 计分。

**为什么是「一次收齐 5 题」而不是「逐题追问」**（本卡的口径，记交付说明）：
问卷如果走逐题 CLARIFY，5 题会直接吃光编排层 `MAX_CLARIFY_ROUNDS = 2` 的额度 ——
用户答到第 3 题就被「转人工」。而 `tools/_wealth_risk.py` 的 `_score()` 本就要求
`answers` **恰好包含 5 题**（多一题少一题都拒），所以「一次收齐」才是与工具层契约对齐的形态。
代价是交互层要出 5 个输入控件 —— 这与 `payee_flow` 的「弹表单」是同一种做法，不是新机制。

两条收答案的通路：
1. **界面表单**（主路径）：`interfaces/web/app.py` 弹问卷 → 调本模块 `submit_assessment`；
2. **一句话**（兜底）：`parse_answers()` 把「年龄35，经验1-3年，…」解析成 5 题 —— 保住
   命令行 / IM / API 三个入口。**解析在代码里做，不让 LLM 抽槽位算术**（铁律 1）：
   LLM 只负责把用户那句话原样放进 `answers` 槽位，认得出多少由本模块的纯函数决定。

分层：本模块属编排层，只调 `agent/` / `tools/` / `data.dao`（审计，与 `orchestrator` 同口径）；
`interfaces/` 只调本模块，不直连 `tools/`。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Mapping

from data import dao

from agent import templates
from tools import wealth as wealth_tool
from tools._wealth_risk import QUESTION_SCORE_RULE
from tools.schemas import ToolResult

logger = logging.getLogger(__name__)

INTENT = "risk_assess"
#: 规格 §2 表第 108 行 + §5：`assess_risk` = **L0**（只读）。不落库、不发确认卡（与 payee_flow 的 L1 不同）。
TIER = "L0"
#: 工具名（轨迹与审计用；与 `read_routes` 里的名字一致）
TOOL = "assess_risk"
#: 聊天触发时回的引导语：**唯一来源是 `templates.T_RISK_PROMPT`**（文案统一在模板层，
#: 与 `payee_flow.PROMPT` 各写一份不同 —— 这里不再抄第二份，避免两处漂移）。
PROMPT = templates.T_RISK_PROMPT

#: 5 题的展示题干与选项文案（**纯文案**，分值不在这里 —— 分值唯一来源是工具层的 `QUESTION_SCORE_RULE`）。
#: 选项顺序必须与 `QUESTION_SCORE_RULE` 的元组下标 0..4 一一对应（第 i 项 → i+1 分）。
QUESTIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "experience": ("您的投资经验有多久？", ("没有", "不到 1 年", "1-3 年", "3-5 年", "5 年以上")),
    "income": ("您的收入稳定程度？", ("很不稳定", "一般", "稳定", "很稳定", "极其稳定")),
    "drawdown": ("您能承受的最大亏损（回撤）是？", ("不接受任何亏损", "5% 以内", "10% 以内",
                                                "20% 以内", "20% 以上")),
    "horizon": ("您计划的投资期限是？", ("3 个月以内", "半年", "1 年", "3 年", "5 年以上")),
    "age": ("您的年龄？（请填写周岁，须年满 18 岁）", ()),
}

#: 一句话解析用的同义词表：key = 题号，value = (归一化后的文案, 分值下标)。
#: 只认这些写法 —— 认不出就整题算缺，交给调用方走 CLARIFY 追问（**不猜**）。
_ANSWER_ALIASES: dict[str, dict[str, int]] = {
    "experience": {"没有": 0, "无": 0, "不到1年": 1, "1年以内": 1, "1-3年": 2, "1到3年": 2,
                   "3-5年": 3, "3到5年": 3, "5年以上": 4, "超过5年": 4},
    "income": {"很不稳定": 0, "一般": 1, "稳定": 2, "很稳定": 3, "极其稳定": 4, "非常稳定": 4},
    "drawdown": {"不接受任何亏损": 0, "不接受亏损": 0, "5%以内": 1, "10%以内": 2,
                 "20%以内": 3, "20%以上": 4},
    "horizon": {"3个月以内": 0, "3个月": 0, "半年": 1, "1年": 2, "一年": 2, "3年": 3, "三年": 3,
                "5年以上": 4, "超过5年": 4},
}

#: 各题在自然语言里的称呼（一句话解析时用来定位"这句话说的是哪一题"）
_QUESTION_WORDS: dict[str, tuple[str, ...]] = {
    "experience": ("经验", "投资经验"),
    "income": ("收入", "收入稳定"),
    "drawdown": ("回撤", "亏损", "承受"),
    "horizon": ("期限", "投资期限", "投资时长"),
    "age": ("年龄", "岁"),
}

#: 年龄题的**锚点词**：只有段里出现它，才把段里的数字当年龄（防止「经验1-3年」的 1 被当岁数）
_AGE_ANCHORS = ("年龄", "岁", "周岁")
#: 从一句话里抠出「年龄 35」「35 岁」这类数字
_AGE_PATTERN = re.compile(r"(\d{1,3})")
#: 用于切分「年龄35，经验1-3年，…」这类多题一句话的段。**只按标点切，不按空白切** ——
#: 选项文案本身可能含空格（实测缺陷：按 `\s` 切会把「3 个月以内」拆成「3」「个月以内」两段）。
_SEPARATOR = re.compile(r"[，,；;、]+")


def _audit(trace_id: str, session_id: str, result: str, error_code: str | None,
           params: dict) -> None:
    """写一条审计（铁律 5）。`params` 只放**非敏感**字段：问卷答案是个人的财务画像，
    但它是本功能的输入契约（工具层也把它原样放进 facts），故按需记录 —— 不含任何凭证。"""
    try:
        dao.insert_audit(trace_id, session_id, actor="agent", intent=INTENT, tool=TOOL,
                         params_json={"intent": INTENT, **params}, risk_level=TIER,
                         permission_tier=TIER, result=result, error_code=error_code)
    except Exception:                                    # noqa: BLE001 —— 审计隔离于回执（同 orchestrator）
        logger.exception("写审计失败（回执照常返回，但必须排查）：trace=%s", trace_id)


#: 各题的**短名**（追问时用；`agent/templates.SLOT_CN` 认得它们，会翻成人话）。
QUESTION_KEYS_CN: dict[str, str] = {
    "experience": "投资经验", "income": "收入稳定性", "drawdown": "可承受亏损",
    "horizon": "投资期限", "age": "年龄",
}


def missing_questions(answers: Mapping[str, object]) -> list[str]:
    """还缺哪几题（返回**短名**，按 `QUESTIONS` 的登记顺序）。齐全 → `[]`。

    追问时经 `templates.clarify_missing` 渲染 —— 短名在 `SLOT_CN` 里有中文对照，
    不会露出 `experience` 这种内部键名。
    """
    return [QUESTION_KEYS_CN[key] for key in QUESTIONS if key not in answers]


def parse_answers(text: str) -> dict[str, int]:
    """把一句话解析成 `answers`（**认不出的题不出现**，由调用方判缺失 → CLARIFY）。

    支持的写法（示例）：「年龄 35，经验 1-3 年，收入稳定，回撤 10% 以内，期限 1 年」。
    解析规则：
    - 按标点/空白切段，每段先看命中哪个题号的**称呼词**（`_QUESTION_WORDS`），
      再在该段里找同义词表里的选项文案；
    - 「年龄」特判：段里直接抠数字，落进工具层 `AGE_BANDS` 的哪一段就是哪一分（**复用工具层分段，
      不复制副本** —— 计分口径只有 `tools/_wealth_risk.py` 一份）；
    - 一段里认不出题号或认不出选项 → 丢弃该段（宁可少认，绝不猜）。
    """
    answers: dict[str, int] = {}
    for chunk in _SEPARATOR.split(text.strip()):
        if not chunk:
            continue
        key = _match_question(chunk)
        if key is None or key in answers:
            continue
        score = _match_age(chunk) if key == "age" else _match_option(key, chunk)
        if score is not None:
            answers[key] = score
    return answers


def _match_question(chunk: str) -> str | None:
    """这一段在说哪一题？命中多个称呼词时取**最靠左**的那个（"投资经验"优先于"投资期限"）。"""
    best: tuple[int, str] | None = None
    for key, words in _QUESTION_WORDS.items():
        for word in words:
            at = chunk.find(word)
            if at >= 0 and (best is None or at < best[0]):
                best = (at, key)
    return best[1] if best else None


def _match_age(chunk: str) -> int | None:
    """从「年龄 35」「35 岁」这类段里抠出**岁数本身**。

    ⚠️ 与另外 4 题**口径不同**：那 4 题存的是**选项下标 0..4**，而 `age` 存的是**岁数**——
    工具层 `_score()` 对 age 的处理是 `next(score for low, high, score in AGE_BANDS if low <= age < high)`，
    它拿 `answers["age"]` 当岁数去查 `AGE_BANDS`，**不是**当下标用。
    （实测缺陷：这里曾返回 `band - 1` 折算成的下标，于是「年龄35」被送成 `1` → 判成 1 岁 → 未成年拒绝。）

    **必须有年龄锚点**（`_AGE_ANCHORS` 里任一词）才认数字 —— 否则「经验1-3年」里的 `1`
    会被当成 1 岁。锚点位置决定往哪边找数字：中文里「35 岁」是**数字在前**、「年龄 35」是
    **数字在后**，两种写法都要认（实测缺陷：只往锚点后面找，「35岁」这种写法抠不到）。
    """
    anchor = next((word for word in _AGE_ANCHORS if word in chunk), None)
    if anchor is None:
        return None
    at = chunk.find(anchor)
    for candidate in (chunk[at + len(anchor):], chunk[:at]):     # 先往后找（「年龄35」），再往前找（「35岁」）
        found = _AGE_PATTERN.search(candidate)
        if found is not None:
            age = int(found.group(1))
            if 0 < age < 200:
                return age
    return None


def _match_option(key: str, chunk: str) -> int | None:
    """在该段里找同义词表命中的选项；多个命中时取**最长**的那个（"很稳定"优先于"稳定"）。

    比对前**去掉段内空白**：选项文案里有「5% 以内」这种带空格的写法，用户却常写「5%以内」——
    去掉空白后两边同形（实测缺陷：带空格比不带空格匹配不上，用户明明写对了却被判缺题）。
    """
    flat = _strip_spaces(chunk)
    hits = [(alias, idx) for alias, idx in _ANSWER_ALIASES[key].items() if _strip_spaces(alias) in flat]
    if not hits:
        return None
    return max(hits, key=lambda item: len(_strip_spaces(item[0])))[1]


def _strip_spaces(text: str) -> str:
    """去掉所有空白（含全角空格），用于选项文案的宽松比对。"""
    return re.sub(r"[\s\u3000]+", "", text)


def count_covers_every_question(answers: Mapping[str, object]) -> bool:
    """五题是否齐全（`_score()` 的硬要求：多一题少一题都拒）。"""
    return set(answers) == set(QUESTION_SCORE_RULE) | {"age"}


def answers_ready(raw: object) -> bool:
    """`answers` 是否可以直接交付工具层计分（**两种形态都认**）。

    形态一：已解析的 dict —— 看五题齐不齐；
    形态二：LLM 抽出来的**一句话** —— 用 `parse_answers` 就地解析一遍看齐不齐（纯函数、无副作用）。
    编排层在 `SLOT_FILL` **之前**就要判"该弹问卷还是直接算"，那时拿到的是形态二，
    所以判断不能只看 dict（实测缺陷：`count_covers_every_question("年龄35，…")` 对字符串恒为 False，
    导致用户已经把 5 题全说了、系统还是弹问卷）。
    """
    return len(_parsed(raw)) == len(QUESTIONS)


def answers_started(raw: object) -> bool:
    """用户是否**至少说出了一个**答案（用于区分"什么都没说"与"说了一部分"）。

    - 什么都没说 → 回引导语 + 界面弹问卷（最省事的路径）；
    - 说了一部分（1..4 题）→ 让它落进只读路径的 SLOT_FILL，走 CLARIFY **追问缺的那几题**，
      而不是让他重填整张卷（实测缺陷：曾一律回引导语，用户说了一半却被要求从头填）。
    """
    return bool(_parsed(raw))


def _parsed(raw: object) -> dict:
    """把两种形态的 `answers` 统一解析成 dict（形态一原样返回，形态二交给 `parse_answers`）。"""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        return parse_answers(raw)
    return {}


def _reply(result: ToolResult) -> tuple[str, bool, str | None]:
    """工具结果 → 回执 `(reply, degraded, error_code)`；数字全部来自事实包（铁律 2）。"""
    if not result.ok:
        code = result.error_code if isinstance(result.error_code, str) else None
        return templates.tool_error(code, result.message), False, code
    facts = result.facts
    text = templates.render(INTENT, {**facts, "answers": facts.get("answers", {})})
    if templates.verify_numbers(text, result.facts):      # §7 数字校验器（同一条防线）
        logger.warning("测评回执出现 facts 之外的数字，降级为不含数字的措辞：%s", facts["risk_level"])
        return templates.T_RISK_RESULT_FALLBACK, True, None
    return text, False, None


def submit_assessment(answers: Mapping[str, object], *, session_id: str) -> "Turn":
    """表单提交入口（界面只调这一个函数）：PRECHECK → EXECUTE → VERIFY_NUMBERS → AUDIT。

    **L0 只读**：没有 CONFIRM_CARD、没有 OTP、没有工具层写库（`assess_risk` 只写进程内私有存储）。
    缺题/非法答案/年龄限制都由**工具层**拒（`INVALID_ARGUMENT` / `FORBIDDEN`），本模块不重复判定
    （铁律 1：口径只有工具层一份），只把结果如实说给用户。

    返回编排层 `Turn`：`intent=risk_assess` / `tool_calls=["assess_risk"]` / `tier="L0"` /
    `executed` 仅在工具成功时 True。**不新增任何 Turn 字段**（界面靠 `intent` 与 `reply` 渲染）。
    """
    from agent.orchestrator import Turn                  # 局部 import：避免 orchestrator ↔ risk_flow 循环

    trace_id = f"trace-{uuid.uuid4().hex[:12]}"
    states = ["IDLE", "SLOT_FILL", "PRECHECK", "EXECUTE", "VERIFY_NUMBERS", "REPLY", "AUDIT"]
    result = wealth_tool.assess_risk(dict(answers))
    reply, degraded, error_code = _reply(result)
    _audit(trace_id, session_id, "success" if result.ok else "error", error_code,
           {"answers": sorted(answers), "risk_level": result.facts.get("risk_level") if result.ok else None})
    return Turn(trace_id=trace_id, intent=INTENT, confidence=1.0, states=states,
                tool_calls=[TOOL], reply=reply, degraded=degraded, error_code=error_code,
                tier=TIER, executed=result.ok)
