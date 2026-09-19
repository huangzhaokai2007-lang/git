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
import contextlib
import sys
from collections.abc import Iterator

from agent import orchestrator
from agent.orchestrator import Turn

#: 离线替身的规则表：关键词 → (意图, 槽位)。命中即用；都不命中 → `out_of_scope`（编排层按低置信度追问）。
#: 这里**只做"从话里认意图/槽位"**（LLM 的活），不碰任何金额/权限/执行判断。
#: 转账那条固定用**新收款人**（张小美，非白名单）：基础档 L2 + OTP —— 现场白天/夜间都一样，
#: 用白名单收款人则会因"夜间因子"在夜里变 L2、白天变 L1，演示与断言会随时刻漂移。
OFFLINE_RULES: tuple[tuple[tuple[str, ...], str, dict], ...] = (
    (("余额", "多少钱"), "balance_query", {}),
    (("花了多少", "账单", "消费", "支出"), "bill_analysis", {}),
    (("订阅",), "subscription_list", {}),
    (("流水", "交易记录"), "txn_query", {}),
    (("异常", "可疑"), "anomaly_check", {}),
    (("理财", "推荐"), "wealth_recommend", {}),
    (("转",), "transfer_single", {"payee": "张小美", "amount": 100}),
)
#: 相对时间词（替身"抽槽位"用；归一化仍由 `agent/period.py` 做，替身不重复判断）
PERIOD_WORDS = ("上个月", "上月", "本月", "这个月", "上上个月")


def offline_verdict(text: str) -> tuple[str, dict]:
    """确定性替身：返回 `(意图, 槽位)`（模拟分类器输出，不涉及权限/金额判定）。"""
    for keywords, intent, slots in OFFLINE_RULES:
        if any(word in text for word in keywords):
            filled = dict(slots)
            if intent in ("bill_analysis", "anomaly_check"):
                filled["period"] = next((word for word in PERIOD_WORDS if word in text), "本月")
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
            intent, slots = offline_verdict(str(user))
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
