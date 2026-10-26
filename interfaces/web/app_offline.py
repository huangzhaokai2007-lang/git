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

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def activate() -> None:
    """装配离线替身，并让它在进程生命周期内持续生效。"""
    import sys as _sys
    # 仓库根必须排在 interfaces/web 之前，否则顶层的 `app` 包会被 web/app.py 遮蔽。
    # （`app/` 现已带 __init__.py 成为普通包，这里再把顺序钉死，双保险。）
    _root = str(Path(__file__).resolve().parents[2])
    if _root in _sys.path:
        _sys.path.remove(_root)
    _sys.path.insert(0, _root)

    from app import cli

    # 本文件**刻意不改 `offline_verdict`**：此前在这里把「一个关键词都没命中但给出了时间区间」
    # 的说法兜成 `txn_query`（「查一下五月份」），那份逻辑已**下沉**到 `app/cli.offline_verdict`
    # —— 留在网页端的下场是 CLI/API/IM 三个入口同一句话一律答「没太理解」，
    # 正是本文件开头记下的"双份口径必漂移"教训的第二次应验。这里只负责把替身挂上。

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
