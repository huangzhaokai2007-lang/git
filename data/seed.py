"""合成银行数据生成器（任务卡 02）。

全部为**合成数据**：不含真实姓名、身份证号、手机号或完整卡号，也不含真实机构名。
- 用户 张三（138****0001，KYC=L2）+ 储蓄/信用 2 个账户 + 3 张卡（其中 1 张已挂失）。
- 账期 2025-09-01 ~ 2026-09-30（**13 个账期月** = 完整一年 + 锚点当月），故**每个月**的账单
  与流水都查得到：每月 49 条常规流水（637 条）+ 12 条植入流水（6 条异常 + 1 条年费 +
  5 条已取消订阅的历史扣费）= 649 条，末月尚未过完扣掉 4 条（见下）= **645 条**。
- 末月（2026-09）只生成到 `AS_OF`（09-12）：今天之后的流水**还没发生**，塞进库里等于给账期
  编未来交易 —— 查「本月账单」时看着就像幻觉。故扣费日/还款日落在今天之后的 4 条不生成
  （信用卡还款 2 条 + 月订 2 条），当月 45 条。
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
from datetime import date, timedelta
from pathlib import Path

from data.db import TABLES, init_db, reset_db, transaction

logger = logging.getLogger(__name__)

SEED = 20260912                                            # 固定随机种子
AS_OF = date(2026, 9, 12)                                  # 数据集的"今天"（固定常量，跨天复现）
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH") or "var/bank.db")   # 卡 20-C：库住 var/，不混进代码目录 data/
DATA_START, DATA_END, MONTHS = date(2025, 9, 1), date(2026, 12, 31), 16
#: 2026-11-01（用户要求）：账期由 13 个月扩到 16 个月（2025-09 ~ 2026-12），保证演示界面里
#: 选任意一个月（含今年 10/11/12 月）都有分类数据、都能拉出饼图；锚点 AS_OF 仍是 9-12（演示时钟）。
USER_ID, SAVINGS_ID, CREDIT_ID = "u_zhangsan_0001", "acc_savings_0001", "acc_credit_0001"
CREDIT_LIMIT = 3_000_000                                   # 信用额度 30,000.00 元（分）
SALARY = 950_000                                           # 月薪 9,500.00 元（分）
SALARY_DAY, REPAY_DAY = 10, 15                              # 每月发薪日 / 信用卡还款日
CREDIT_OPENING_BILL = 125_000                              # 期初账单 1,250.00 元 → 首月还款额
OPENING_BALANCES: dict[str, int] = {SAVINGS_ID: 2_865_000, CREDIT_ID: -CREDIT_OPENING_BILL}

#: 卡：id, account_id, 掩码卡号, type, credit_limit, single_limit, daily_limit, status
CARD_ROWS = (
    ("card_savings_0001", SAVINGS_ID, "6222 **** **** 0001", "savings", None, 500_000, 2_000_000, "normal"),
    ("card_credit_0002", CREDIT_ID, "6222 **** **** 0002", "credit", CREDIT_LIMIT, 1_000_000, 2_000_000, "normal"),
    ("card_savings_0003", SAVINGS_ID, "6222 **** **** 0003", "savings", None, 200_000, 500_000, "lost"),
)
#: 收款人：id, 姓名, 掩码手机号, 银行, 是否白名单, 标签(逗号分隔), 白名单专属额度(分, None=无)
#: 李四两位用于测试收款人歧义；v2 起带**标签**，演示可用「给我妈转 200」「给房东转 3000」指代。
PAYEE_ROWS = (
    # 元组：id, 姓名, 脱敏手机, 银行, 是否白名单, 标签, 白名单专属额度（分）, 白名单有效期（ISO 日期/None，仅展示）
    ("payee_0001", "李四", "139****1001", "招商银行", 1, "同事", 100_000, "2026-11-15"),   # 即将到期（演示）
    ("payee_0002", "李四", "139****1002", "工商银行", 0, "同学", None, None),
    ("payee_0003", "王五", "137****2003", "建设银行", 1, "房东", 100_000, "2026-12-31"),   # 有效期内
    ("payee_0004", "张小美", "136****3004", "中国银行", 0, "妈妈,家人", None, None),
    ("payee_0005", "赵六", "135****4005", "农业银行", 0, "朋友", None, None),          # 从未转账 → last_used_ts 为 NULL
)
#: 收款人的**多张银行卡**（2026-10-31 用户需求）：id, payee_id, 银行, 尾号, 卡种, 是否默认
#: 每位 1–3 张、可以不同银行；**每人的第一张是默认卡**（转账不选卡时就用它）。
PAYEE_CARD_ROWS = (
    ("pc_0001_a", "payee_0001", "招商银行", "1001", "储蓄卡", 1),
    ("pc_0001_b", "payee_0001", "工商银行", "1004", "储蓄卡", 0),
    ("pc_0002_a", "payee_0002", "工商银行", "1002", "储蓄卡", 1),
    ("pc_0002_b", "payee_0002", "建设银行", "1005", "信用卡", 0),
    ("pc_0003_a", "payee_0003", "建设银行", "2003", "储蓄卡", 1),
    ("pc_0003_b", "payee_0003", "农业银行", "2007", "储蓄卡", 0),
    ("pc_0003_c", "payee_0003", "中国银行", "2009", "信用卡", 0),
    ("pc_0004_a", "payee_0004", "中国银行", "3004", "储蓄卡", 1),
    ("pc_0005_a", "payee_0005", "农业银行", "4005", "储蓄卡", 1),
    ("pc_0005_b", "payee_0005", "交通银行", "4008", "储蓄卡", 0),
)
TRANSFER_PAYEES = (("payee_0001", "李四"), ("payee_0002", "李四"), ("payee_0003", "王五"), ("payee_0004", "张小美"))
#: 理财产品：id, 名称, 风险等级, 期限天, 预期年化（百分数数值，3.20 = 3.20%）, 起购金额（分）
#: **收益率按 2025–2026 年真实市场水平校准**（2026-10-29）：货基 1.1–1.3%（2025 末中位数 1.23%）、
#: 三年大额存单 ~2%、纯债 2.6–2.9%、固收+ 4% 上下、红利/REITs 5% 上下、
#: 权益类 6–9%、雪球类票息 13–17%（非保本，票息不是预期收益）。
PRODUCT_ROWS = (
    ("prod_r1_mmf", "稳盈货币基金", "R1", 0, 1.15, 100),
    ("prod_r1_cash", "现金管理增强", "R1", 0, 1.30, 1_000),
    ("prod_r1_cd", "大额存单三年期", "R1", 1095, 2.05, 2_000_000),
    ("prod_r2_bond30", "短债 30 天", "R2", 30, 2.10, 1_000),
    ("prod_r2_bond90", "稳健纯债 90 天", "R2", 90, 2.60, 10_000),
    ("prod_r2_bond180", "添利定开 180 天", "R2", 180, 2.90, 100_000),
    ("prod_r2_cd_index", "同业存单指数 7 天持有", "R2", 7, 1.75, 1_000),
    ("prod_r2_sharkfin", "鲨鱼鳍结构性存款 365 天（保本）", "R2", 365, 3.20, 100_000),
    ("prod_r3_balance", "均衡配置 180 天", "R3", 180, 3.80, 500_000),
    ("prod_r3_mixed365", "混合优选 365 天", "R3", 365, 4.40, 1_000_000),
    ("prod_r3_div", "红利优选 365 天", "R3", 365, 5.00, 1_000_000),
    ("prod_r3_secondary", "固收+ 二级债基 365 天", "R3", 365, 4.10, 10_000),
    ("prod_r3_reits", "公募 REITs 精选三年期", "R3", 1095, 5.40, 100_000),
    ("prod_r3_gold", "黄金 ETF 联接", "R3", 0, 6.20, 1_000),
    ("prod_r4_asia", "海外配置 365 天", "R4", 365, 5.80, 2_000_000),
    ("prod_r4_quant720", "量化增强 720 天", "R4", 720, 6.00, 5_000_000),
    ("prod_r4_growth540", "成长先锋 540 天", "R4", 540, 6.80, 3_000_000),
    ("prod_r4_snowball", "雪球型收益凭证 730 天（挂钩中证 500 · 非保本）", "R4", 730, 13.50, 10_000_000),
    ("prod_r4_convertible", "可转债增强 365 天", "R4", 365, 5.80, 500_000),
    ("prod_r4_qdii", "QDII 纳斯达克 100", "R4", 365, 8.60, 100_000),
    ("prod_r5_theme", "主题轮动 365 天", "R5", 365, 7.50, 5_000_000),
    ("prod_r5_growth", "成长精选三年期", "R5", 1095, 8.20, 10_000_000),
    ("prod_r5_pe", "私募精选 730 天", "R5", 730, 9.20, 20_000_000),
    ("prod_r5_cta", "CTA 管理期货 365 天", "R5", 365, 7.40, 10_000_000),
    ("prod_r5_airbag", "气囊雪球收益凭证 550 天（挂钩中证 1000 · 非保本）", "R5", 550, 17.20, 10_000_000),
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


def _month_last_day(index: int) -> int:
    """第 index 个月可用于生成流水的最大日子：锚点当月只到 `AS_OF`，其余月一律 28。

    不用当月真实天数，是为了让各月口径一致（28 天以内每个月都有，跨月比较才公平）；
    而锚点当月**尚未过完**，生成今天之后的流水等于给账期编未来交易。
    """
    return AS_OF.day if _month_of(index) == (AS_OF.year, AS_OF.month) else 28


def _sample_days(rng: random.Random, pool: range, count: int) -> list[int]:
    """在 `pool` 里取 `count` 个日子：够抽就不重复，天数不够（末月）才允许同日多笔。"""
    return rng.sample(pool, count) if len(pool) >= count else rng.choices(pool, k=count)


def _month_drafts(index: int, rng: random.Random, prev_credit: int,
                  payee_usage: dict[str, str]) -> tuple[list[TxnDraft], int]:
    """第 index 个月的常规流水；返回 (草稿, 本月信用卡消费合计)。

    整月 49 条 = 40 条批次消费 + 工资 + 3 笔转账 + 3 笔月订 + 信用卡还款 2 条；
    锚点当月只到今天，扣费日/还款日落在今天之后的几条不生成（当月 45 条）。
    批次按 rng.sample 取日内不重复的日子、同商户每月最多 2 笔，故正常数据不命中"同商户 1 小时 ≥3 笔"。
    """
    year, month = _month_of(index)
    last = _month_last_day(index)
    pool = range(1, last + 1)
    drafts: list[TxnDraft] = []
    credit_cents = 0
    for batch in MONTHLY_BATCHES:
        for i, day in enumerate(_sample_days(rng, pool, batch.count)):
            amount = rng.randint(batch.low_yuan, batch.high_yuan) * 100
            drafts.append(TxnDraft(batch.account_id, _ts(date(year, month, day), rng.randint(7, 21),
                                                         rng.randint(0, 59)), -amount, batch.category,
                                   batch.channel, batch.merchants[i % len(batch.merchants)],
                                   batch.memos[i % len(batch.memos)]))
            credit_cents += amount if batch.account_id == CREDIT_ID else 0
    if SALARY_DAY <= last:                                   # 发薪日还没到 → 本月工资尚未入账
        drafts.append(TxnDraft(SAVINGS_ID, _ts(date(year, month, SALARY_DAY), 9, 30), SALARY, "工资", "转账",
                               "示例科技有限公司", "月薪"))
    for slot, day in enumerate(_sample_days(rng, pool, 3)):
        payee_id, name = TRANSFER_PAYEES[(index * 3 + slot) % len(TRANSFER_PAYEES)]
        ts = _ts(date(year, month, day), rng.randint(7, 21), rng.randint(0, 59))
        drafts.append(TxnDraft(SAVINGS_ID, ts, -rng.randint(100, 800) * 100, "转账", "转账", name, f"转账给{name}"))
        payee_usage[payee_id] = max(payee_usage.get(payee_id, ""), ts)
    monthly = [s for s in SUBSCRIPTIONS
               if s.status == "active" and s.cycle == "monthly" and s.charge_day <= last]
    for sub in monthly:                                      # 扣费日未到 → 本月还没扣
        drafts.append(TxnDraft(SAVINGS_ID, _ts(date(year, month, sub.charge_day), 10, 5), -sub.amount,
                               "订阅", "代扣", sub.merchant, f"{sub.merchant} 月费"))
    if REPAY_DAY <= last:                                    # 还款日未到 → 本期账单还没还
        repay_ts = _ts(date(year, month, REPAY_DAY), 20, 40)  # 还款额 = 上月信用卡消费合计
        drafts.append(TxnDraft(SAVINGS_ID, repay_ts, -prev_credit, "转账", "转账", "信用卡还款", "信用卡还款"))
        drafts.append(TxnDraft(CREDIT_ID, repay_ts, prev_credit, "转账", "转账", "信用卡还款", "信用卡还款"))
    return drafts, credit_cents


def _ref_no(ts: str, account_id: str, seq: int) -> str:
    """流水号（纯数字）：`YYYYMMDDHHMM` + 4 位账户内序号 + 账户位（1=储蓄 2=信用），全局不重号。"""
    compact = ts[:16].replace("-", "").replace("T", "").replace(":", "")
    return f"{compact}{seq:04d}{1 if account_id == SAVINGS_ID else 2}"


#: 交易状态分布（F02 明细表）：绝大多数已完成；临近锚点的少量"待处理"（还没清算完），
#: 少量"失败/退款"让状态列有信息量。
#: **独立种子**的 Random（不扰动主流水的随机数序列），且**每次 finalize 现开一条流** ——
#: 模块级单例会跨两次 seed 累积状态（`test_two_runs_are_row_by_row_identical` 当场抓到）。
_PENDING_WINDOW = timedelta(days=7)


def _status(ts: str, amount: int, rng: random.Random) -> str:
    """一笔流水的交易状态：规则确定性分配（同一条流水永远同状态）。"""
    roll = rng.random()
    if date.fromisoformat(ts[:10]) > AS_OF - _PENDING_WINDOW and roll < 0.18:
        return "pending"                       # 最近几天的交易可能还在清算
    if roll < 0.035:
        return "failed"
    if amount < 0 and roll < 0.07:
        return "refunded"                      # 支出后才谈得上退款
    return "done"


def _finalize(drafts: list[TxnDraft]) -> tuple[list[tuple], dict[str, int]]:
    """按 (ts, 生成顺序) 排列并逐笔累加得 balance_after；返回 (txn 行, 账户期末余额)。"""
    balances = dict(OPENING_BALANCES)
    seq: dict[str, int] = {}
    rows: list[tuple] = []
    status_rng = random.Random(f"{SEED}:status")
    for _, draft in sorted(enumerate(drafts), key=lambda pair: (pair[1].ts, pair[0])):
        balances[draft.account_id] += draft.amount
        seq[draft.account_id] = seq.get(draft.account_id, 0) + 1
        rows.append((f"txn-{'s' if draft.account_id == SAVINGS_ID else 'c'}-{seq[draft.account_id]:04d}",
                     draft.account_id, draft.ts, draft.amount, "in" if draft.amount > 0 else "out",
                     draft.counterparty, draft.category, draft.channel, draft.memo,
                     balances[draft.account_id],
                     _ref_no(draft.ts, draft.account_id, seq[draft.account_id]),
                     _status(draft.ts, draft.amount, status_rng)))
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
                                              "tags", "whitelist_quota", "last_used_ts", "whitelist_expire"),
                              tuple((p[0], USER_ID, p[1], p[2], p[3], p[4], p[5], p[6],
                                     payee_usage.get(p[0]), p[7]) for p in PAYEE_ROWS))
    counts["payee_card"] = _insert(conn, "payee_card",
                                   ("id", "payee_id", "user_id", "bank", "card_no_mask",
                                    "card_type", "is_default", "created_at"),
                                   tuple((c[0], c[1], USER_ID, c[2],
                                          f"6222 **** **** {c[3]}", c[4], c[5], AS_OF.isoformat())
                                         for c in PAYEE_CARD_ROWS))
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
                                          "category", "channel", "memo", "balance_after", "ref_no", "status"),
                            tuple(rows))
    counts["audit_log"] = _insert(conn, "audit_log", ("id", "trace_id", "session_id", "ts", "actor", "intent",
                                                      "tool", "params_json", "risk_level", "permission_tier",
                                                      "result", "error_code"),
                                  (("audit_seed_0001", "trace-seed-0001", "session-seed-0001",
                                    f"{AS_OF.isoformat()}T00:00:00", "system", "seed", "data.seed",
                                    json.dumps({"seed": SEED, "txn_count": len(rows)}), "L0", "L0",
                                    "success", None),))
    counts["risk_event"] = 0
    return counts


def generate(db_path: str | Path = DEFAULT_DB_PATH, *, reset: bool = False,
             bind_cards: bool = False) -> dict[str, int]:
    """生成全套合成数据，返回各表写入行数；目标库非空时抛 `ValueError`（覆盖请传 reset=True）。

    `bind_cards=True`：额外把 6 张预设卡预绑到演示用户（`agent_card_binding`），
    让评委第一次打开网页就能在「管卡」看到卡、转账时能选付款卡。默认 False ——
    测试夹具（`seeded` / `demo.build_temp_db`）走共享的 `generate()`，不预绑，
    以免影响「空绑定 → 绑卡」这条用例基线。
    """
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
            if bind_cards:
                from data import card_binding, card_binding_seed, card_limit_seed
                from guard import card_credentials
                #: 绑卡/密码表都是**懒建**的（`agent_card_secret`、`agent_card_binding`），
                #: 先把 schema 建出来，再补密码、补额度演练卡，最后才绑——否则撞「no such table」/外键。
                card_binding.ensure_schema(conn)
                card_binding_seed.provision(conn, card_credentials.hash_pin)
                card_limit_seed.provision(conn, card_credentials.hash_pin)
                counts["agent_card_binding"] = card_binding_seed.provision_bindings(conn)
                #: 演示库：所有卡恢复为未挂失（评委现场不想要一张已挂失的卡挡路）
                counts["card_normalized"] = card_binding_seed.reset_card_status(conn)
        logger.info("已在 %s 写入合成数据：%s", path, counts)
        return counts
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI：`uv run python -m data.seed --reset`。"""
    parser = argparse.ArgumentParser(prog="python -m data.seed", description="生成 2025-09~2026-09 的合成银行数据")
    parser.add_argument("--reset", action="store_true", help="先清库再生成（覆盖已有数据）")
    parser.add_argument("--bind-cards", action="store_true", help="额外把 6 张预设卡预绑到演示用户（网页演示用）")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="库文件路径，默认取环境变量 DB_PATH")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        counts = generate(args.db, reset=args.reset, bind_cards=args.bind_cards)
    except (ValueError, sqlite3.Error) as exc:
        logger.error("生成失败：%s", exc)
        return 2
    logger.info("账期 %s ~ %s，共 %d 条流水，%d 张表已就绪", DATA_START, DATA_END, counts["txn"], len(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


