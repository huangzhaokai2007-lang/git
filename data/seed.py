"""合成银行数据生成器（任务卡 02）。

全部为**合成数据**：不含真实姓名、身份证号、手机号或完整卡号，也不含真实机构名。
- 用户 张三（138****0001，KYC=L2）+ 储蓄/信用 2 个账户 + 3 张卡（其中 1 张已挂失）。
- 账期 2025-09-01 ~ 2026-08-31（12 个月）：每月 49 条常规流水（588 条）+ 12 条植入流水
  （6 条异常 + 1 条年费 + 5 条已取消订阅的历史扣费）= **600 条**。
- 期初余额见 `OPENING_BALANCES`：每个账户按 (ts, id) 顺序累加 amount 得到 balance_after，
  账户最终 balance 即最后一笔的 balance_after —— "流水合计 = 余额变动"可独立复算。
- `AS_OF` 是数据集的"今天"（固定常量，跨天亦可复现）；订阅下次扣费日以它为准落在未来 30 天内。
- 随机数只来自 local `random.Random(SEED)`，不触碰全局 random，故两次生成逐行一致。

"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from data.db import TABLES, init_db, reset_db, transaction

logger = logging.getLogger(__name__)

SEED = 20260912                                            # 固定随机种子
AS_OF = date(2026, 9, 12)                                  # 数据集的"今天"（固定常量，跨天复现）
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH") or "var/bank.db")   # 卡 20-C：库住 var/，不混进代码目录 data/
DATA_START, DATA_END, MONTHS = date(2025, 9, 1), date(2026, 8, 31), 12
USER_ID, SAVINGS_ID, CREDIT_ID = "u_zhangsan_0001", "acc_savings_0001", "acc_credit_0001"
CREDIT_LIMIT = 3_000_000                                   # 信用额度 30,000.00 元（分）
SALARY = 950_000                                           # 月薪 9,500.00 元（分）
CREDIT_OPENING_BILL = 125_000                              # 期初账单 1,250.00 元 → 首月还款额
OPENING_BALANCES: dict[str, int] = {SAVINGS_ID: 2_865_000, CREDIT_ID: -CREDIT_OPENING_BILL}

#: 卡：id, account_id, 掩码卡号, type, credit_limit, single_limit, daily_limit, status
CARD_ROWS = (
    ("card_savings_0001", SAVINGS_ID, "6222 **** **** 0001", "savings", None, 500_000, 2_000_000, "normal"),
    ("card_credit_0002", CREDIT_ID, "6222 **** **** 0002", "credit", CREDIT_LIMIT, 1_000_000, 2_000_000, "normal"),
    ("card_savings_0003", SAVINGS_ID, "6222 **** **** 0003", "savings", None, 200_000, 500_000, "lost"),
)
#: 收款人：id, 姓名, 掩码手机号, 银行, 是否白名单；李四两位用于测试收款人歧义
PAYEE_ROWS = (
    ("payee_0001", "李四", "139****1001", "招商银行", 1),
    ("payee_0002", "李四", "139****1002", "工商银行", 0),
    ("payee_0003", "王五", "137****2003", "建设银行", 1),
    ("payee_0004", "张小美", "136****3004", "中国银行", 0),
    ("payee_0005", "赵六", "135****4005", "农业银行", 0),   # 从未转账 → last_used_ts 为 NULL
)
TRANSFER_PAYEES = (("payee_0001", "李四"), ("payee_0002", "李四"), ("payee_0003", "王五"), ("payee_0004", "张小美"))
#: 理财产品：id, 名称, 风险等级, 期限天, 预期年化（百分数数值，3.20 = 3.20%）, 起购金额（分）
PRODUCT_ROWS = (
    ("prod_r1_mmf", "稳盈货币基金", "R1", 0, 2.10, 100),
    ("prod_r2_bond90", "稳健纯债 90 天", "R2", 90, 3.20, 10_000),
    ("prod_r2_bond180", "添利定开 180 天", "R2", 180, 3.60, 100_000),
    ("prod_r3_mixed365", "混合优选 365 天", "R3", 365, 4.50, 1_000_000),
    ("prod_r4_quant720", "量化增强 720 天", "R4", 720, 6.20, 5_000_000),
    ("prod_r5_growth", "成长精选三年期", "R5", 1095, 8.50, 10_000_000),
)
HOLDING_ROWS = (
    ("holding_0001", "prod_r1_mmf", 2_000_000, "2025-10-15", "held"),
    ("holding_0002", "prod_r2_bond90", 5_000_000, "2026-03-20", "held"),
)


@dataclass(frozen=True)
class Subscription:
    id: str
    merchant: str
    amount: int
    cycle: str
    status: str
    charge_day: int
    next_charge_date: str
    charged_months: tuple[int, ...]   # 账期内实际扣费的月份序号（0 = 2025-09）


SUBSCRIPTIONS: tuple[Subscription, ...] = (
    Subscription("sub_0001", "云音乐", 1_500, "monthly", "active", 18, "2026-09-18", tuple(range(MONTHS))),
    Subscription("sub_0002", "星辰视频", 2_500, "monthly", "active", 25, "2026-09-25", tuple(range(MONTHS))),
    Subscription("sub_0003", "云端网盘", 1_200, "monthly", "active", 2, "2026-10-02", tuple(range(MONTHS))),
    Subscription("sub_0004", "好买会员", 19_800, "yearly", "active", 8, "2026-10-08", (2,)),
    Subscription("sub_0005", "健悦健身", 19_900, "monthly", "cancelled", 8, "2025-12-10", (0, 1, 2)),
    Subscription("sub_0006", "轻食工坊", 3_900, "monthly", "cancelled", 25, "2026-03-02", (4, 5)),
)


@dataclass(frozen=True)
class Batch:
    category: str
    account_id: str
    count: int
    channel: str
    low_yuan: int
    high_yuan: int                      # 单笔金额 = rng.randint(low_yuan, high_yuan) * 100 分
    merchants: tuple[str, ...]
    memos: tuple[str, ...]


_CAFE = ("悦食快餐", "南山米线店", "科技园面馆", "晨光咖啡", "老友火锅", "街角烧烤", "壹号茶餐厅", "甜心烘焙")
_RIDE = ("科技园地铁站", "公交一卡通", "网约车平台", "共享单车")
_SHOP = ("好买商城", "淘乐优选", "极速优选")
_UTILITY = ("自来水公司", "电力公司", "燃气公司")
_CC_SHOP = ("云上超市", "乐购生活", "果鲜生", "轻食沙拉")
MONTHLY_BATCHES: tuple[Batch, ...] = (
    Batch("餐饮", SAVINGS_ID, 16, "二维码", 8, 68, _CAFE, ("午饭", "晚饭", "早餐", "同事聚餐", "外卖", "加餐")),
    Batch("交通", SAVINGS_ID, 7, "二维码", 2, 25, _RIDE, ("通勤", "打车", "地铁", "骑行")),
    Batch("电商", SAVINGS_ID, 6, "卡", 39, 599, _SHOP, ("日用品", "数码配件", "服饰", "网购")),
    Batch("生活缴费", SAVINGS_ID, 3, "代扣", 45, 380, _UTILITY, ("水费", "电费", "燃气费")),
    Batch("电商", CREDIT_ID, 8, "卡", 20, 500, _CC_SHOP, ("超市采购", "生鲜", "零食", "日用")),
)


@dataclass(frozen=True)
class Anomaly:
    ts: str
    amount: int
    category: str
    channel: str
    counterparty: str
    memo: str
    tags: tuple[str, ...]


#: 6 条植入异常，覆盖 4 类规则：凌晨大额 2（同时金额突增）+ 陌生商户 1 + 短时高频 3
ANOMALIES: tuple[Anomaly, ...] = (
    Anomaly("2026-03-14T02:13:00", -380_000, "电商", "卡", "星域数码专营店", "凌晨下单", ("night_large", "amount_jump")),
    Anomaly("2026-07-05T03:41:00", -465_000, "电商", "二维码", "星域数码专营店", "深夜扫码付款", ("night_large", "amount_jump")),
    Anomaly("2026-05-21T14:22:00", -128_000, "电商", "二维码", "深圳汇通数码专营店", "陌生商户付款", ("stranger_merchant",)),
    Anomaly("2026-06-18T15:02:00", -68_000, "电商", "二维码", "快闪便利店", "扫码支付", ("velocity",)),
    Anomaly("2026-06-18T15:11:00", -72_000, "电商", "二维码", "快闪便利店", "扫码支付", ("velocity",)),
    Anomaly("2026-06-18T15:19:00", -76_000, "电商", "二维码", "快闪便利店", "扫码支付", ("velocity",)),
)


@dataclass(frozen=True)
class TxnDraft:                                  # id 与 balance_after 由 _finalize() 按时间顺序分配
    account_id: str
    ts: str
    amount: int                         # 分；正=入账 负=出账
    category: str
    channel: str
    counterparty: str
    memo: str


def _ts(day: date, hour: int, minute: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00"


def _month_of(index: int) -> tuple[int, int]:
    return 2025 + (8 + index) // 12, (8 + index) % 12 + 1     # 月份序号（0 = 2025-09）→ (年, 月)


def _month_drafts(index: int, rng: random.Random, prev_credit: int,
                  payee_usage: dict[str, str]) -> tuple[list[TxnDraft], int]:
    """第 index 个月的 49 条常规流水；返回 (草稿, 本月信用卡消费合计)。
    批次按 rng.sample 取日内不重复的日子、同商户每月最多 2 笔，故正常数据不命中"同商户 1 小时 ≥3 笔"。"""
    year, month = _month_of(index)
    drafts: list[TxnDraft] = []
    credit_cents = 0
    for batch in MONTHLY_BATCHES:
        for i, day in enumerate(rng.sample(range(1, 29), batch.count)):
            amount = rng.randint(batch.low_yuan, batch.high_yuan) * 100
            drafts.append(TxnDraft(batch.account_id, _ts(date(year, month, day), rng.randint(7, 21),
                                                         rng.randint(0, 59)), -amount, batch.category,
                                   batch.channel, batch.merchants[i % len(batch.merchants)],
                                   batch.memos[i % len(batch.memos)]))
            credit_cents += amount if batch.account_id == CREDIT_ID else 0
    drafts.append(TxnDraft(SAVINGS_ID, _ts(date(year, month, 10), 9, 30), SALARY, "工资", "转账",
                           "示例科技有限公司", "月薪"))
    for slot, day in enumerate(rng.sample(range(1, 29), 3)):
        payee_id, name = TRANSFER_PAYEES[(index * 3 + slot) % len(TRANSFER_PAYEES)]
        ts = _ts(date(year, month, day), rng.randint(7, 21), rng.randint(0, 59))
        drafts.append(TxnDraft(SAVINGS_ID, ts, -rng.randint(100, 800) * 100, "转账", "转账", name, f"转账给{name}"))
        payee_usage[payee_id] = max(payee_usage.get(payee_id, ""), ts)
    for sub in (s for s in SUBSCRIPTIONS if s.status == "active" and s.cycle == "monthly"):
        drafts.append(TxnDraft(SAVINGS_ID, _ts(date(year, month, sub.charge_day), 10, 5), -sub.amount,
                               "订阅", "代扣", sub.merchant, f"{sub.merchant} 月费"))
    repay_ts = _ts(date(year, month, 15), 20, 40)          # 还款额 = 上月信用卡消费合计
    drafts.append(TxnDraft(SAVINGS_ID, repay_ts, -prev_credit, "转账", "转账", "信用卡还款", "信用卡还款"))
    drafts.append(TxnDraft(CREDIT_ID, repay_ts, prev_credit, "转账", "转账", "信用卡还款", "信用卡还款"))
    return drafts, credit_cents


def _finalize(drafts: list[TxnDraft]) -> tuple[list[tuple], dict[str, int]]:
    """按 (ts, 生成顺序) 排列并逐笔累加得 balance_after；返回 (txn 行, 账户期末余额)。"""
    balances = dict(OPENING_BALANCES)
    seq: dict[str, int] = {}
    rows: list[tuple] = []
    for _, draft in sorted(enumerate(drafts), key=lambda pair: (pair[1].ts, pair[0])):
        balances[draft.account_id] += draft.amount
        seq[draft.account_id] = seq.get(draft.account_id, 0) + 1
        rows.append((f"txn-{'s' if draft.account_id == SAVINGS_ID else 'c'}-{seq[draft.account_id]:04d}",
                     draft.account_id, draft.ts, draft.amount, "in" if draft.amount > 0 else "out",
                     draft.counterparty, draft.category, draft.channel, draft.memo, balances[draft.account_id]))
    return rows, balances


def _insert(conn: sqlite3.Connection, table: str, columns: tuple[str, ...], rows: tuple) -> int:
    placeholders = ", ".join("?" * len(columns))
    conn.executemany(f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({placeholders})', rows)
    return len(rows)


def _write(conn: sqlite3.Connection, rows: list[tuple], balances: dict[str, int],
           payee_usage: dict[str, str]) -> dict[str, int]:
    """一个事务里写完全部 10 张表，返回各表行数（储蓄可用额 = 余额，信用可用额 = 额度 + 余额）。"""
    savings, credit = balances[SAVINGS_ID], balances[CREDIT_ID]
    charges = {s.merchant: [r for r in rows if r[6] == "订阅" and r[5] == s.merchant] for s in SUBSCRIPTIONS}
    counts = {"user": _insert(conn, "user", ("id", "name", "phone", "kyc_level"),
                              ((USER_ID, "张三", "138****0001", "L2"),))}
    counts["account"] = _insert(conn, "account", ("id", "user_id", "type", "balance", "available", "status"),
                                ((SAVINGS_ID, USER_ID, "savings", savings, savings, "active"),
                                 (CREDIT_ID, USER_ID, "credit", credit, CREDIT_LIMIT + credit, "active")))
    counts["card"] = _insert(conn, "card", ("id", "user_id", "account_id", "card_no_mask", "type", "credit_limit",
                                            "single_limit", "daily_limit", "status"),
                             tuple((c[0], USER_ID, *c[1:]) for c in CARD_ROWS))
    counts["payee"] = _insert(conn, "payee", ("id", "user_id", "name", "phone", "bank", "is_whitelist",
                                              "last_used_ts"),
                              tuple((p[0], USER_ID, *p[1:], payee_usage.get(p[0])) for p in PAYEE_ROWS))
    counts["subscription"] = _insert(conn, "subscription", ("id", "user_id", "merchant", "amount", "cycle",
                                                            "next_charge_date", "source_txn_id", "status"),
                                     tuple((s.id, USER_ID, s.merchant, s.amount, s.cycle, s.next_charge_date,
                                            max(charges[s.merchant], key=lambda r: r[2])[0], s.status)
                                           for s in SUBSCRIPTIONS))
    counts["wealth_product"] = _insert(conn, "wealth_product", ("id", "name", "risk_level", "term_days",
                                                                "expected_yield", "min_amount"), PRODUCT_ROWS)
    counts["holding"] = _insert(conn, "holding", ("id", "user_id", "product_id", "amount", "purchase_date",
                                                 "status"), tuple((h[0], USER_ID, *h[1:]) for h in HOLDING_ROWS))
    counts["txn"] = _insert(conn, "txn", ("id", "account_id", "ts", "amount", "direction", "counterparty",
                                          "category", "channel", "memo", "balance_after"), tuple(rows))
    counts["audit_log"] = _insert(conn, "audit_log", ("id", "trace_id", "session_id", "ts", "actor", "intent",
                                                      "tool", "params_json", "risk_level", "permission_tier",
                                                      "result", "error_code"),
                                  (("audit_seed_0001", "trace-seed-0001", "session-seed-0001",
                                    f"{AS_OF.isoformat()}T00:00:00", "system", "seed", "data.seed",
                                    json.dumps({"seed": SEED, "txn_count": len(rows)}), "L0", "L0",
                                    "success", None),))
    counts["risk_event"] = 0
    return counts


def generate(db_path: str | Path = DEFAULT_DB_PATH, *, reset: bool = False) -> dict[str, int]:
    """生成全套合成数据，返回各表写入行数；目标库非空时抛 `ValueError`（覆盖请传 reset=True）。"""
    path = Path(db_path)
    conn = reset_db(path) if reset else init_db(path)
    try:
        existing = sum(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES)
        if existing:
            raise ValueError(f"{path} 里已有 {existing} 行数据：请先清库（--reset）再生成")
        rng = random.Random(SEED)
        payee_usage: dict[str, str] = {}
        drafts: list[TxnDraft] = []
        prev_credit = CREDIT_OPENING_BILL                      # 首月还的是期初账单
        for index in range(MONTHS):
            month_drafts, prev_credit = _month_drafts(index, rng, prev_credit, payee_usage)
            drafts.extend(month_drafts)
        for sub in SUBSCRIPTIONS:                              # 年费 + 已取消订阅的历史扣费
            if not (sub.status == "active" and sub.cycle == "monthly"):
                label = "年费" if sub.cycle == "yearly" else "月费"
                for index in sub.charged_months:
                    year, month = _month_of(index)
                    drafts.append(TxnDraft(SAVINGS_ID, _ts(date(year, month, sub.charge_day), 10, 5), -sub.amount,
                                           "订阅", "代扣", sub.merchant, f"{sub.merchant} {label}"))
        drafts.extend(TxnDraft(SAVINGS_ID, a.ts, a.amount, a.category, a.channel, a.counterparty, a.memo)
                      for a in ANOMALIES)                      # 6 条植入异常
        rows, balances = _finalize(drafts)
        with transaction(conn):
            counts = _write(conn, rows, balances, payee_usage)
        logger.info("已在 %s 写入合成数据：%s", path, counts)
        return counts
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI：`uv run python -m data.seed --reset`。"""
    parser = argparse.ArgumentParser(prog="python -m data.seed", description="生成 2025-09~2026-08 的合成银行数据")
    parser.add_argument("--reset", action="store_true", help="先清库再生成（覆盖已有数据）")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="库文件路径，默认取环境变量 DB_PATH")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        counts = generate(args.db, reset=args.reset)
    except (ValueError, sqlite3.Error) as exc:
        logger.error("生成失败：%s", exc)
        return 2
    logger.info("账期 %s ~ %s，共 %d 条流水，%d 张表已就绪", DATA_START, DATA_END, counts["txn"], len(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


