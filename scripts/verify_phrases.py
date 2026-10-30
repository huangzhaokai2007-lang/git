"""话术清单的**真分类器**复核：逐条打印「话术 → 意图 → 槽位」。

用法：uv run python scripts/verify_phrases.py（需要 .env 配好 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）。
不进 erify.sh：它依赖**真模型**（联网 + 花钱），而一键验收必须离线可跑。
对应文档：docs/话术清单-转账收款域.md
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PHRASES = [
    # ---- 转账 ----
    ("给王五转 100 元", "transfer_single"),
    ("给王五转一百块", "transfer_single"),
    ("转 50 给李四", "transfer_single"),
    ("给房东转 300", "transfer_single"),                    # 标签指代
    ("给wangwu转 50", "transfer_single"),                   # 拼音
    ("给张小美转 20", "transfer_single"),
    ("转给赵六 66 元", "transfer_single"),
    ("给王五转 1000 元", "transfer_single"),                # 走白名单额度/限额
    # ---- 预约转账 ----
    ("明天给王五转 200 元", "transfer_scheduled"),
    ("下周一给房东转 500", "transfer_scheduled"),
    ("2027-10-10 给王五转 100 元", "transfer_scheduled"),
    ("每个月末给房东转 500 元，到年底", "transfer_scheduled"),
    ("每月 15 号给张小美转 300", "transfer_scheduled"),
    ("我的预约有哪些", "scheduled_list"),
    ("把预约改到后天上午", "scheduled_modify"),
    ("现在就把那笔预约执行了", "scheduled_run"),
    ("取消明天那笔预约", "scheduled_cancel"),
    # ---- 收款人 ----
    ("我的收款人有哪些", "payee_list"),
    ("我有几个收款人", "payee_list"),
    ("把张小美加入白名单，额度 500 元，有效期到 2026-12-31", "payee_whitelist"),
    ("把王五加入白名单", "payee_whitelist"),
    ("把张小美的专属额度改成 800 元", "payee_whitelist_edit"),
    ("把王五移出白名单", "payee_whitelist_remove"),
    ("把赵六拉黑", "payee_blacklist"),
    ("把赵六移出黑名单", "payee_unblacklist"),
    ("删除收款人赵六", "payee_delete"),
    ("给张小美加个备注：老同学别转错", "payee_note"),
    ("给王五加标签：房东", "payee_tags"),
    ("给张小美记一下卡号 6222000000000001", "payee_card_no"),
    # ---- 群收款 ----
    ("我和王五群收款 300 元", "aa_collect"),
    ("向王五和赵六群收款 300 元", "aa_collect"),
    ("发起群收款", "aa_collect"),
    ("王五 100 赵六 200 一起群收款", "aa_collect"),
    ("我的群收款有哪些", "aa_list"),
    ("王五那 100 已经付了", "aa_mark_paid"),
    ("提醒赵六付群收款", "aa_remind"),
    ("把这笔群收款撤销", "aa_cancel"),
]


def main() -> int:
    from agent import classifier

    bad = 0
    for text, expect in PHRASES:
        try:
            out = classifier.classify(text, None)
        except Exception as exc:                      # noqa: BLE001
            print(f"ERR  {text:34s} {type(exc).__name__}: {exc}")
            bad += 1
            continue
        mark = "ok " if out.intent == expect else "❌ "
        if out.intent != expect:
            bad += 1
        print(f"{mark} {text:34s} -> {out.intent:20s} {json.dumps(out.slots, ensure_ascii=False)}")
    print(f"\n合计 {len(PHRASES)} 条，不符 {bad} 条")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
