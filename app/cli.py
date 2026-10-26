"""命令行入口（卡 19）：一句话 → 编排层 → 回执 + 轨迹。

用法（仓库根目录）：

    uv run python -m app.cli "帮我看看上个月花了多少"
    uv run python -m app.cli --offline "给王五转100元" --session demo-1     # 无网/无 key 的确定性演示
    uv run python -m app.cli --offline "确认" --session demo-1
    uv run python -m app.cli --offline "123456" --session demo-1           # 验证码由工具层比对

分层（CLAUDE.md 铁律）：本层**只调 `agent/`**（`orchestrator.handle`），不 import `tools`/`data`/`guard`，
不判意图、不判权限、不算数字、不写 SQL —— 与网页端（`interfaces/web`）、评测入口（`interfaces/api`）、
IM 通道（`interfaces/im`）用的是**同一个编排层**。

`--offline`（演示/验收用）：把"理解意图"这一步换成**确定性替身**（关键词 → 意图 + 槽位）。
按铁律 1，LLM 的职责只有"理解意图 + 措辞"，所以替身**只替换这一步**：金额、余额、权限档、
确认卡、幂等执行、审计全部仍走真实的工具层与护栏 —— 与 `scripts/redteam.py` 的"分类器被劫持"口径一致。
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import re
import sys
from collections.abc import Iterator
from datetime import date

from agent import orchestrator
from agent.orchestrator import Turn
from agent.period import anchor_date, month_shift

#: 离线替身的规则表：关键词 → (意图, 槽位)。命中即用；都不命中 → `out_of_scope`（编排层按低置信度追问）。
#: 这里**只做"从话里认意图/槽位"**（LLM 的活），不碰任何金额/权限/执行判断。
#: 转账那条固定用**新收款人**（张小美，非白名单）：基础档 L2 + OTP —— 现场白天/夜间都一样，
#: 用白名单收款人则会因"夜间因子"在夜里变 L2、白天变 L1，演示与断言会随时刻漂移。
#: **两条顺序注释**（前面的赢，同类至少各栽过一次）：
#: - 「异常」必须排在「花了多少/账单/消费/支出」**前面**：F03 的原话「上个月有什么异常支出？」
#:   同时含"异常"与"支出"，排在后面会被账单分析截走（实测：问异常却答环比 -32%）——
#:   与下面 F10「测评」必须排在「理财」前面同一个道理：更具体的意图优先。
#: - 「流水/交易记录」排在「异常」后面同理（"有没有异常交易"含"交易"但缺"记录"，暂不冲突，留个记号）。
#: - 「一份账单」这类**明确要一份报告**的说法归 bill_report，必须排在 bill_analysis **前面** ——
#:   两者共享「账单」，顺序反了就被分析截走（问"出一份上个月的账单"却答环比 -32%，实机复现）。
OFFLINE_RULES: tuple[tuple[tuple[str, ...], str, dict], ...] = (
    (("余额", "多少钱"), "balance_query", {}),
    (("异常", "可疑"), "anomaly_check", {}),
    #: F03：账单**总结**（T5 generate_bill_report）必须排在账单**分析**（T3 analyze_spending）**前面** ——
    #: 两者共享「账单」这个词，顺序反了就被分析截走（问"总结账单"却答环比，与上面「异常」被
    #: 「支出」截走是同一类缺陷）。触发词取「总结/汇总/报告/一份账单」这类**明确要一份报告**
    #: 的说法：说「上个月花了多少」「账单怎么样」仍走分析，各归各位。
    (("账单总结", "账单报告", "月账单", "总结", "汇总", "报告"), "bill_report", {}),
    (("花了多少", "账单", "消费", "支出"), "bill_analysis", {}),
    (("订阅",), "subscription_list", {}),
    #: F02：触发词要覆盖**日常说法**，不只"流水"这个书面词 —— 实测「帮我看看上个月的钱都花哪了」
    #: 判成 out_of_scope（"没太理解您的意思"），用户当场以为功能没做出来。
    (("流水", "交易记录", "明细", "花哪", "钱都花", "收支"), "txn_query", {}),
    #: F10：「测评」必须排在「理财」**前面** —— 「帮我做个理财风险测评」同时含两个关键词，
    #: 按顺序先匹配的那条赢；测评是更具体的意图（它有自己的问卷流程），理财推荐是泛的。
    (("测评", "风险评估"), "risk_assess", {}),
    (("理财", "推荐"), "wealth_recommend", {}),
    (("转",), "transfer_single", {"payee": "张小美", "amount": 100}),
)
#: 相对时间词（替身"抽槽位"用；归一化仍由 `agent/period.py` 做，替身不重复判断）
PERIOD_WORDS = ("上个月", "上月", "本月", "这个月", "上上个月")

#: 显式月份：`2026-09` / `2026/09` / `2026年9月`。
#: 负向前瞻 `(?!\s*[-/月.]\s*\d)` 用来排除紧跟日的完整日期 —— 否则「2026-09-12」会被截成整月。
_EXPLICIT_MONTH = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*月?(?!\s*[-/月.]\s*\d)")
#: 不带年份的「9月」
_BARE_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)")
#: **多账期跨度**说法（「最近三个月」「近半年」）：工具层的 `analyze_spending` 只吃**单账期**
#: `YYYY-MM`，没有跨月聚合能力。认出它 → 原样返回该说法，`agent/period.resolve_period` 认不出
#: → 编排层 CLARIFY 追问，而不是把它静默吞成锚点当月（那正是"问三个月、答一个月"的静默错月）。
#: 结尾的「半年」单列一支：中文里「近半个月」是 15 天，绝不能按 6 个月算 —— 故不把「半」并进
#: 数字表 `_CN_NUM`，只在它**直接**跟在「最近/近」后面、且不带「个/日/天」时才当半年。
_SPAN_MONTHS = re.compile(r"(?:最近|近)\s*(?:(\d+|[一二三四五六七八九十两]+)\s*个?月|半年)")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_HALF_YEAR_MONTHS = 6
#: 只给了**年**没给月（「2026年的账单」）：负向前瞻排掉「2026年9月」——那种由 `_EXPLICIT_MONTH`
#: 先接走。工具层认 `YYYY` 年度账期，故这里原样给年份，不缩成某一个月。
_YEAR_ONLY = re.compile(r"(?<!\d)(\d{4})\s*年(?!\s*\d{1,2}\s*月)")
#: 「最近一年」/「近 12 个月」→ **年度账期**：工具层 `analyze_spending` 原生认 `YYYY`（全年聚合），
#: 比缩成单月诚实得多，也正好是"数据扩到一年"后用得上的说法。
_YEAR_SPAN = re.compile(r"(?:最近|近)\s*(?:一\s*年|12\s*个?月|十二\s*个?月)")
#: 编排层 `orchestrator._with_date_context` 追加的时间口径尾巴（铁律 7：作为**数据**附在
#: user 消息里，不进 system prompt）。替身抽槽位前**必须先剥掉它**：
#: 它长得跟"用户说了一个完整日期"一模一样 —— 不剥就会被 `len(iso)==1` 分支当成「当天」，
#: 于是「2025-09 的流水」被解析成 2026-09-12（锚点当天），逐月查询全答成同一个月。
_DATE_CONTEXT = re.compile(r"\n*（当前日期：\s*\d{4}-\d{2}-\d{2}\s*）\s*$")


def _strip_date_context(text: str) -> str:
    """去掉编排层追加的「（当前日期：…）」尾巴，只留用户真正说的话。"""
    return _DATE_CONTEXT.sub("", text).strip()


def _span_months(match: re.Match) -> int:
    """「最近 N 个月」→ N；「最近半年」→ 6；都不匹配 → 0（调用方据此决定要不要拦）。"""
    raw = match.group(1)
    if raw is None:                                          # 命中「半年」支（无数字）
        return _HALF_YEAR_MONTHS
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)


def period_from_text(text: str) -> str:
    """从话里抽账期：**显式月份优先**，其次相对词（`PERIOD_WORDS`），都没有 → 锚点当月。

    为什么必须认显式月份：认不出就静默回落锚点当月，于是问「2025-12 花了多少」答成
    「2026-09 一共支出…」 —— 数字来自真实事实包、看着完全像对的，但**月份是错的**，
    正是卡 17b 记下的那类"静默答错月份"。数据集现已覆盖 13 个账期月，逐月可查才有意义。

    「9月」这类不带年份的说法按锚点年取值，且取到未来月份（> 锚点当月）时**回退一年** ——
    今天 9 月说「11月」指的是去年 11 月，不是还没到的今年 11 月。

    **先剥日期上下文**（`_strip_date_context`）：否则编排层附的「（当前日期：2026-09-12）」
    会抢在用户原话前面被认成"显式月份"，逐月查询就全答成锚点当月。
    """
    today = anchor_date()
    text = _strip_date_context(text)

    if _YEAR_SPAN.search(text):
        return str(today.year)

    #: 多账期跨度（≥2 个月）→ 原样退回说法文字，让编排层追问"您想查哪个月"，**绝不缩成一个月**；
    #: 「最近一个月」等价于本月，放它走下面的正常分支。
    span = _SPAN_MONTHS.search(text)
    if span and _span_months(span) >= 2:
        return span.group(0)

    found = _EXPLICIT_MONTH.search(text)
    if found and 1 <= int(found.group(2)) <= 12:
        return f"{int(found.group(1)):04d}-{int(found.group(2)):02d}"
    year_only = _YEAR_ONLY.search(text)
    if year_only:
        return year_only.group(1)
    bare = _BARE_MONTH.search(text)
    if bare and 1 <= int(bare.group(1)) <= 12:
        month = int(bare.group(1))
        year = today.year - 1 if (today.year, month) > (today.year, today.month) else today.year
        return f"{year:04d}-{month:02d}"
    by_length = sorted(PERIOD_WORDS, key=len, reverse=True)
    return next((word for word in by_length if word in text), "本月")


def dates_from_text(text: str) -> dict:
    """从话里抽 F02 的 `date_from` / `date_to`（替身"抽槽位"用，**非**业务实现）。

    支持：「2026-08-01 到 2026-08-31」「8月1日到8月31日」「上个月」「本月」「最近 N 天」「最近」
    「2026-09」「2026年9月」「9月」（整月）。
    相对说法一律以**数据锚点**为今天（`agent.period.anchor_date`）—— 与编排层告诉模型的口径一致。

    **为什么下沉到替身而不是只打在网页端**：此前只有 `interfaces/web/app_offline.py` 猴子补丁
    补日期，于是「上个月的流水」在**网页上能演、CLI/API/IM 三个入口一律卡在「请补充起始日期、
    结束日期」的追问里** —— 替身抽槽位的能力应当四个入口一致（与 session_memory 的教训同源）。

    **先剥日期上下文**：编排层会在话尾附上「（当前日期：2026-09-12）」，它跟"用户说了一个完整
    日期"长得一模一样 —— 不剥掉就会被下面的 `len(iso)==1` 分支当成「查当天」，逐月查询全军覆没。
    """
    text = _strip_date_context(text)
    iso = re.findall(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if len(iso) >= 2:                                            # 一对完整日期 → 直接用
        (y1, m1, d1), (y2, m2, d2) = iso[0], iso[-1]
        return {"date_from": f"{y1}-{int(m1):02d}-{int(d1):02d}",
                "date_to": f"{y2}-{int(m2):02d}-{int(d2):02d}"}

    cn = re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)  # 「8月1日到8月31日」
    if len(cn) >= 2:
        year = int(iso[0][0]) if iso else anchor_date().year
        (m1, d1), (m2, d2) = cn[0], cn[-1]
        return {"date_from": f"{year}-{int(m1):02d}-{int(d1):02d}",
                "date_to": f"{year}-{int(m2):02d}-{int(d2):02d}"}

    def whole_month(y: int, m: int) -> dict:
        return {"date_from": f"{y}-{m:02d}-01",
                "date_to": f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"}

    #: ⚠️ **不许用 `date.today()`**：真实时钟跑在 2026-10，而合成数据窗口的末月是 2026-08
    #: （锚点 `AS_OF` = 2026-09-12）→ 「上个月」解析成 2026-09 → 一笔都没有，演示当场翻车。
    today = anchor_date()
    if "上上个月" in text:
        y, m = (today.year, today.month - 2) if today.month > 2 else (today.year - 1, today.month + 10)
        return whole_month(y, m)
    if "上个月" in text or "上月" in text:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return whole_month(y, m)
    if "本月" in text or "这个月" in text:
        return whole_month(today.year, today.month)

    #: 「最近 N 个月」（N≥2）/「最近一年」**必须在「最近」兜底分支之前**处理：否则会被当成
    #: "没给天数"，缩水成近 30 天 —— 问三个月答一个月。流水查询支持跨月区间
    #: （`_month_windows` 分窗），所以这里能老实给足整段。
    if _YEAR_SPAN.search(text):                              # 滚动 12 个自然月：首月 1 号 → 锚点今天
        return {"date_from": f"{month_shift(-11)}-01", "date_to": today.isoformat()}
    span = _SPAN_MONTHS.search(text)
    if span and (count := _span_months(span)) >= 2:
        return {"date_from": f"{month_shift(-(count - 1))}-01", "date_to": today.isoformat()}

    days = re.search(r"最近\s*(\d+)\s*天", text)
    if days:
        return {"date_from": date.fromordinal(today.toordinal() - int(days.group(1)) + 1).isoformat(),
                "date_to": today.isoformat()}
    if "最近" in text:                                           # 没给天数 → 默认近 30 天
        return {"date_from": date.fromordinal(today.toordinal() - 29).isoformat(),
                "date_to": today.isoformat()}
    if len(iso) == 1:                                            # 单个日期 → 当天
        y, m, d = iso[0]
        day = f"{y}-{int(m):02d}-{int(d):02d}"
        return {"date_from": day, "date_to": day}
    if len(cn) == 1:                                             # 单个「8月1日」→ 当天（锚点年）
        m, d = cn[0]
        day = f"{today.year}-{int(m):02d}-{int(d):02d}"
        return {"date_from": day, "date_to": day}

    #: 整月说法（「2026-09 的流水」「9月的明细」）：月份交给 `period_from_text` 统一认，
    #: 这里只把它摊成首末日 —— 不再各写一份 `YYYY-MM` 正则，避免两边口径漂移。
    period = period_from_text(text)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        year, month = int(period[:4]), int(period[5:])
        return {"date_from": f"{period}-01",
                "date_to": f"{period}-{calendar.monthrange(year, month)[1]:02d}"}
    if re.fullmatch(r"\d{4}", period):                          # 「2026年的流水」→ 全年区间
        year = int(period)
        return {"date_from": f"{period}-01-01",
                "date_to": f"{period}-12-{calendar.monthrange(year, 12)[1]:02d}"}
    return {}


def offline_verdict(text: str) -> tuple[str, dict]:
    """确定性替身：返回 `(意图, 槽位)`（模拟分类器输出，不涉及权限/金额判定）。

    相对时间词按**长度降序**取：否则「上上个月」会先被子串「上个月」截走 —— 问两个月前、
    答成了上个月，正是卡 17b 记下的那类"静默回落"缺陷（替身也不许这么干）。
    抽出来的词会不会解析成账期，仍由 `agent/period.py` 决定：认不出的相对说法 → 编排层
    CLARIFY 追问，**不猜**（如替身会给出而 period 认不出的「上上个月」）。
    """
    for keywords, intent, slots in OFFLINE_RULES:
        if any(word in text for word in keywords):
            filled = dict(slots)
            if intent in ("bill_analysis", "anomaly_check", "bill_report"):
                filled["period"] = period_from_text(text)
            if intent == "txn_query":                    # F02 要的日期槽位（替身抽，非业务实现）
                filled.update(dates_from_text(text))
            return intent, filled
    return "out_of_scope", {}


@contextlib.contextmanager
def offline_llm() -> Iterator[None]:
    """把 `agent.llm.chat_json` 换成替身：意图分类给规则结果，其余调用一律不可用（润色退回模板原文）。

    离线模式下"润色不可用"是**预期行为**（模板原文本身就是最终回执口径），所以顺手把
    `agent.templates` 的日志级别压到 ERROR —— 否则演示/验收时会被那条 warning 刷屏。
    """
    import logging
    from unittest import mock                              # 仅离线演示路径用到（标准库）

    from agent import classifier, llm, templates

    def chat_json(system: str, user: object, schema: type) -> object:
        if schema is classifier.IntentOut:
            #: `user` 是**消息列表**（含最近几轮追问上下文 + 助手回执），只取最后一条才是
            #: 用户**这一句**说的话。用 `str(user)` 去匹配关键词会把历史回执里的字也匹配进来
            #: → 问完「查一下余额」后再问「上个月的流水」，回执里的「余额」二字还在上下文里
            #: → 后面每一句都被判成 balance_query（一键演示五连问当场串味，实机复现）。
            text = user[-1]["content"] if isinstance(user, list) and user else user
            intent, slots = offline_verdict(str(text))
            return schema(intent=intent, confidence=0.95 if intent != "out_of_scope" else 0.0,
                          slots=slots, missing_slots=[], unsafe_reason=None)
        raise llm.LLMUnavailable("离线演示：除意图分类外不给 LLM 输出")

    quiet = logging.getLogger(templates.__name__)
    previous = quiet.level
    quiet.setLevel(logging.ERROR)
    try:
        with mock.patch.object(llm, "chat_json", chat_json):
            yield
    finally:
        quiet.setLevel(previous)


def render(turn: Turn) -> str:
    """终端渲染：回执 + 一行轨迹（字段与 `POST /api/chat` 同源）。"""
    trail = "  ".join((f"意图={turn.intent}", f"工具={','.join(turn.tool_calls) or '-'}",
                       f"权限档={turn.tier or '-'}", f"已执行={'是' if turn.executed else '否'}",
                       f"trace={turn.trace_id}"))
    lines = [turn.reply, f"  [{trail}]"]
    if turn.error_code:
        lines.append(f"  （错误码 {turn.error_code}：数字/状态均由代码判定，未执行任何写操作）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：跑一次编排层并打印回执；正常返回 0。"""
    parser = argparse.ArgumentParser(prog="app.cli", description="模拟银行智能体 · 命令行入口")
    parser.add_argument("text", help="用户原话，例如 \"查一下余额\"")
    parser.add_argument("--offline", action="store_true",
                        help="离线演示模式：用确定性替身替代 LLM 的\"理解意图\"这一步")
    parser.add_argument("--session", default=None, help="会话 id（多轮确认/验证码用）")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    with offline_llm() if args.offline else contextlib.nullcontext():
        turn = orchestrator.handle(args.text, session_id=args.session)
    print(render(turn))
    return 0


if __name__ == "__main__":                                  # pragma: no cover - CLI 入口
    raise SystemExit(main())
