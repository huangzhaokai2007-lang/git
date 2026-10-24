"""卡 16：网页聊天界面的**离线入口**（没配 `LLM_API_KEY` 时用这一条）。

分层与 `app.py` 完全一致：**不 import `tools/`、不 import `data/`、不写 SQL**，
只是先把"理解意图"这一步换成确定性替身，再 `runpy` 真正的 `interfaces/web/app.py`。

为什么需要它：没有模型配置时 `agent.llm` 抛 `LLMUnavailable`，分类器一律回落
`out_of_scope`，对话框对任何提问都答「没太理解您的意思」——功能是齐全的，只是**演不出来**。
这里挂上 `app/cli.py: offline_llm()`（与 `scripts/demo.py`、`app.cli --offline` 同一份替身实现），
意图识别改由关键词规则出，工具层、权限、回执渲染仍走真实链路。

在仓库自带规则之上只补了**一处**：F02 流水查询的日期槽位抽取（`date_from` / `date_to`）。
`app/cli.py` 的 `OFFLINE_RULES` 不给 `txn_query` 抽日期，问「最近的流水」会一直卡在
「请补充起始日期、结束日期」的追问里。真接模型后由模型抽槽位，下面整段都可删。

跑法：`uv run streamlit run interfaces/web/app_offline.py`
配好 `.env` 后请改回：`uv run streamlit run interfaces/web/app.py`
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def dates_from_text(text: str) -> dict:
    """从话里抽 F02 的 `date_from` / `date_to`（离线演示用，**非**业务实现）。

    支持：「2026-08-01 到 2026-08-31」「8月1日到8月31日」「上个月」「本月」
    「最近 N 天」「最近」。相对说法一律以**数据锚点**为今天（`agent.period.anchor_date`）。
    """
    iso = re.findall(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if len(iso) >= 2:                                            # 一对完整日期 → 直接用
        (y1, m1, d1), (y2, m2, d2) = iso[0], iso[-1]
        return {"date_from": f"{y1}-{int(m1):02d}-{int(d1):02d}",
                "date_to": f"{y2}-{int(m2):02d}-{int(d2):02d}"}

    cn = re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)  # 「8月1日到8月31日」
    if len(cn) >= 2:
        year = int(iso[0][0]) if iso else date.today().year
        (m1, d1), (m2, d2) = cn[0], cn[-1]
        return {"date_from": f"{year}-{int(m1):02d}-{int(d1):02d}",
                "date_to": f"{year}-{int(m2):02d}-{int(d2):02d}"}

    def whole_month(y: int, m: int) -> dict:
        return {"date_from": f"{y}-{m:02d}-01",
                "date_to": f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"}

    #: ⚠️ **不许用 `date.today()`**：真实时钟跑在 2026-10，而合成数据窗口的末月是 2026-08
    #: （锚点 `AS_OF` = 2026-09-12）→ 「上个月」解析成 2026-09 → 一笔都没有，演示当场翻车。
    #: 全系统的口径是「锚点即今天」（orchestrator 也是这么告诉模型的），这里必须同一个锚点。
    #: 局部 import：本文件被 streamlit 直接加载，模块级 import 时 `sys.path` 还没配好。
    from agent.period import anchor_date

    today = anchor_date()
    if "上上个月" in text:
        y, m = (today.year, today.month - 2) if today.month > 2 else (today.year - 1, today.month + 10)
        return whole_month(y, m)
    if "上个月" in text or "上月" in text:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return whole_month(y, m)
    if "本月" in text or "这个月" in text:
        return whole_month(today.year, today.month)

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
    return {}


def activate() -> None:
    """装配离线替身，并让它在进程生命周期内持续生效。"""
    from app import cli

    original_verdict = cli.offline_verdict

    def verdict_with_dates(text: str) -> tuple[str, dict]:
        intent, slots = original_verdict(text)
        if intent == "out_of_scope" and dates_from_text(text):
            intent = "txn_query"                     # 追问后只回日期 → 续接流水查询
        if intent == "txn_query":
            slots = dict(slots, **dates_from_text(text))
        return intent, slots

    cli.offline_verdict = verdict_with_dates

    # ⚠️ 必须保住生成器引用！写成 `offline_llm().__enter__()` 的话，临时生成器会被 GC 回收
    # → 在 yield 处抛 GeneratorExit → `with mock.patch` 的 __exit__ 执行 → patch **静默撤销**，
    # 表现是网页上所有提问又变回 out_of_scope（踩过一次的坑，别改回简写）。
    global _CONTEXT
    _CONTEXT = cli.offline_llm()
    _CONTEXT.__enter__()


_CONTEXT = None


if __name__ == "__main__":
    import runpy
    import sys

    # 顺序与 app.py 一致：先同目录、再仓库根 —— 反了的话仓库根会被顶到后面，
    # `interfaces/web/app.py` 会遮蔽顶层 `app` 包，`from app import cli` 直接 ImportError。
    for _p in (str(Path(__file__).resolve().parent), str(ROOT)):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    activate()
    runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
