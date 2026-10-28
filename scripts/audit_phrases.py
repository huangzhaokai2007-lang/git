#!/usr/bin/env python
"""话术自动排雷（`scripts/audit_phrases.py`）—— 把这几轮人工排查固化成可重复的运行。

**为什么需要它**：本项目最近的 5 个真 bug（空回执、渲染器崩溃、口径分裂、同名误报…）
全是「换一种说法就坏」的类型，而单元测都绿。这个脚本沿着**整条链路**跑一批真实说法：

    意图 → 槽位 → 解析 → 定档 → 确认卡 / 直接执行 → 模板渲染 → 回执

然后检查四件事：
1. 意图有没有落到预期（`--live` 用真模型时才检查；`--offline` 用显式意图）；
2. 该出确认卡的有没有出；
3. **回执不能为空**（空回执 = 界面「没动静」，实测过一次）；
4. 不能出现意外错误码 / 渲染异常。

两种模式：
- `--offline`（默认，CI 用）：注入 `intent_resolver` 直接给定意图与槽位 ——
  **不依赖外网/LLM**，覆盖槽位填充、工具调用、定档、卡面渲染、模板渲染这一整段；
- `--live`：走真实分类器（需要 LLM key），用于**演示前**自查「换种说法还认不认」。

用法：
    uv run python scripts/audit_phrases.py              # 离线全跑
    uv run python scripts/audit_phrases.py --live       # 真模型全跑
    uv run python scripts/audit_phrases.py --list       # 只列用例
退出码：有问题 → 1。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READ, WRITE = "read", "write"

#: 排雷用例：`text` 是用户会说的原话；`intent`/`slots` 是离线模式的既定解析结果。
#: `expect` 该意图集合（live 模式用来判断有没有被判错）。
CASES: list[dict] = [
    #: 2026-10-28：群收款（含我）+ 收款人列表 —— 改名与新卡面之后的说法都要能跑通
    {"text": "我的群收款有哪些", "intent": "aa_list", "slots": {}, "kind": READ},
    {"text": "发起群收款", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五"], "amount": "300"}, "kind": WRITE},
    {"text": "我和王五群收款 300 元", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五"], "amount": "300"}, "kind": WRITE},
    {"text": "向王五和赵六群收款 300 元", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五", "赵六"], "amount": "300"}, "kind": WRITE},
    {"text": "把张小美加入白名单，额度 500 元，有效期到 2026-12-31",
     "intent": "payee_whitelist",
     "slots": {"payee": "张小美", "quota": "500", "expire": "2026-12-31"}, "kind": WRITE},
    {"text": "把王五拉黑", "intent": "payee_blacklist",
     "slots": {"payee": "王五"}, "kind": WRITE},

    # ---- 只读：看模板渲染与「不误执行」 ----
    {"text": "我的收款人有哪些", "intent": "payee_list", "slots": {}, "kind": READ},
    {"text": "查一下我的收款人", "intent": "payee_list", "slots": {}, "kind": READ},
    {"text": "我有几个收款人", "intent": "payee_list", "slots": {}, "kind": READ},
    {"text": "余额", "intent": "balance_query", "slots": {}, "kind": READ},
    {"text": "我卡里还有多少钱", "intent": "balance_query", "slots": {}, "kind": READ},
    {"text": "上月流水", "intent": "txn_query", "slots": {"period": "上个月"}, "kind": READ},
    {"text": "我的理财持仓", "intent": "wealth_holdings", "slots": {}, "kind": READ},
    {"text": "我订了哪些服务", "intent": "subscription_list", "slots": {}, "kind": READ},
    {"text": "我的预约有哪些", "intent": "scheduled_list", "slots": {}, "kind": READ},
    {"text": "我的 AA", "intent": "aa_list", "slots": {}, "kind": READ},
    {"text": "我的送礼订单", "intent": "gift_order_list", "slots": {}, "kind": READ},
    # ---- 转账 ----
    {"text": "给王五转 100 元", "intent": "transfer_single",
     "slots": {"payee": "王五", "amount": "100"}, "kind": WRITE},
    {"text": "给房东转 300", "intent": "transfer_single",
     "slots": {"payee": "房东", "amount": "300"}, "kind": WRITE},
    {"text": "给wangwu转 50", "intent": "transfer_single",
     "slots": {"payee": "wangwu", "amount": "50"}, "kind": WRITE},
    {"text": "给王五转 600 元", "intent": "transfer_single",
     "slots": {"payee": "王五", "amount": "600"}, "kind": WRITE, "allow_error": True},
    # ---- 收款人信息 ----
    {"text": "给王五加标签：房东", "intent": "payee_tags",
     "slots": {"payee": "王五", "tags": "房东"}, "kind": WRITE},
    {"text": "给张小美加个备注：别转错", "intent": "payee_note",
     "slots": {"payee": "张小美", "note": "别转错"}, "kind": WRITE},
    {"text": "给张小美记一下卡号 6222021234567890", "intent": "payee_card_no",
     "slots": {"payee": "张小美", "card_no": "6222021234567890"}, "kind": WRITE},
    {"text": "把张小美加入白名单，额度 300 元，有效期到年底", "intent": "payee_whitelist",
     "slots": {"payee": "张小美", "quota": "300", "expire": "年底"}, "kind": WRITE},
    {"text": "把赵六拉黑", "intent": "payee_blacklist", "slots": {"payee": "赵六"}, "kind": WRITE},
    # ---- AA ----
    {"text": "向王五和赵六 AA 收款 300 元", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五", "赵六"], "amount": "300"}, "kind": WRITE},
    {"text": "跟王五、赵六 AA 收 300", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五", "赵六"], "amount": "300"}, "kind": WRITE},
    {"text": "向王五，妈妈，房东 AA 收款各 30,40,50", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五", "妈妈", "房东"], "amounts": "王五 30、妈妈 40、房东 50"},
     "kind": WRITE},
    {"text": "向王五和赵六 AA 收款 5000 元", "intent": "aa_collect",
     "slots": {"payee_ids": ["王五", "赵六"], "amount": "5000"}, "kind": WRITE,
     "allow_error": True},
    # ---- 定时 ----
    {"text": "2026-11-20 09:00 给王五转 100 元", "intent": "transfer_scheduled",
     "slots": {"payee": "王五", "amount": "100", "schedule": "2026-11-20 09:00"}, "kind": WRITE},
    {"text": "每月15日给房东转 300", "intent": "transfer_scheduled",
     "slots": {"payee": "房东", "amount": "300", "repeat": "每个月"}, "kind": WRITE},
    # ---- 送礼（平台/地址/订单号必填） ----
    {"text": "后天在京东给王五送个礼物，预算 300，送到上海市某某路 1 号，订单号 JD1001",
     "intent": "gift_plan",
     "slots": {"contact": "王五", "date": "后天", "budget": "300", "platform": "京东",
               "address": "上海市某某路 1 号", "order_no": "JD1001"}, "kind": WRITE},
    {"text": "后天给王五送个礼物，预算 300", "intent": "gift_plan",
     "slots": {"contact": "王五", "date": "后天", "budget": "300"}, "kind": WRITE,
     "allow_clarify": True},          # 缺平台/地址/单号 → 追问是**正确**行为
    # ---- 边界 ----
    {"text": "帮我写一首诗", "intent": "out_of_scope", "slots": {}, "kind": READ},
]


def _prepare_db() -> Path:
    """在临时目录造一份合成库，**不碰** var/bank.db。"""
    tmp = Path(tempfile.mkdtemp(prefix="audit-"))
    db = tmp / "bank.db"
    env = {**os.environ, "DB_PATH": str(db)}
    subprocess.run([sys.executable, "-m", "data.seed", "--reset"], cwd=REPO, env=env,
                   check=True, capture_output=True)
    os.environ["DB_PATH"] = str(db)          # 必须在 import data/tools 之前设好
    return db


def _resolver(case: dict):
    """离线模式：把用例里写好的意图/槽位当成「模型输出」。"""
    from agent import classifier

    def resolve(text: str, history=None) -> classifier.IntentOut:
        return classifier.IntentOut(intent=case["intent"], confidence=0.95,
                                    slots=dict(case["slots"]), missing_slots=[])

    return resolve


def run(live: bool = False) -> list[str]:
    """跑一遍全部用例，返回问题清单（空 = 全绿）。"""
    _prepare_db()
    from agent import classifier, confirm_card, orchestrator, payee_picker, session_memory
    from tools._query_common import set_current_user

    set_current_user("u_zhangsan_0001")
    problems: list[str] = []
    rows: list[str] = []

    for index, case in enumerate(CASES):
        sid = f"audit-{index}"
        confirm_card.reset_state()
        session_memory.reset(sid)
        payee_picker.clear(sid)
        try:
            turn = orchestrator.handle(case["text"], session_id=sid,
                                       intent_resolver=None if live else _resolver(case))
        except Exception as exc:                                  # noqa: BLE001
            problems.append(f"{case['text']} → 抛异常 {type(exc).__name__}: {exc}")
            rows.append(f"❌ {case['text'][:34]:36s} 异常 {type(exc).__name__}")
            continue

        note = ""
        if not str(turn.reply or "").strip():
            note = "回执为空（界面会『没动静』）"
        elif turn.intent != case["intent"]:
            note = f"意图 {turn.intent}（期望 {case['intent']}）"
        elif case["kind"] == WRITE and not case.get("allow_clarify") and not case.get("allow_error"):
            if not turn.executed and "CONFIRM_CARD" not in turn.states:
                note = f"既没出确认卡也没执行（states={turn.states}）"
        elif case["kind"] == READ and turn.executed:
            note = "只读意图却执行了写操作"
        if note:
            problems.append(f"{case['text']} → {note}")
        rows.append(f"{'❌' if note else 'ok'} {case['text'][:34]:36s} "
                    f"{turn.intent:20s} t={turn.tier} exec={turn.executed} {note}")

    for row in rows:
        print(row)
    return problems


def _live_cases() -> None:
    """`--live` 前置提示：说明它需要 LLM key。"""
    print("live 模式会用真实分类器（需要 LLM key）；离线模式不需要。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="话术自动排雷（整链路）")
    parser.add_argument("--live", action="store_true", help="用真实分类器（需要 LLM key）")
    parser.add_argument("--list", action="store_true", help="只列出用例")
    args = parser.parse_args(argv)

    if args.list:
        for case in CASES:
            print(f"{case['kind']:5s} {case['intent']:20s} {case['text']}")
        return 0
    if args.live:
        _live_cases()

    print(f"== 话术排雷：{len(CASES)} 条（{'live' if args.live else 'offline'}）==")
    problems = run(live=args.live)
    print()
    if problems:
        print(f"❌ 发现 {len(problems)} 条问题：")
        for item in problems:
            print("  -", item)
        return 1
    print(f"✅ {len(CASES)} 条说法全部通过（意图/确认卡/回执/渲染）")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    raise SystemExit(main())
