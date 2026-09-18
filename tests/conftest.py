"""任务卡 04b：工具层测试的**共享脚手架**（拆分 `test_tools_query` / `test_tools_transfer` 时抽出）。

只放两个测试模块都在用的东西：fixtures（`seeded` / `blank` / `foreign` / `foreign_payee` / `clock`）与
独立口径的断言辅助（`raw` / `money` / `numbers` / `assert_*`）。**测试体一律不进这里**。

为什么用 conftest 而不是每个文件复制一份：拆分前这套辅助在两个文件里各有一份（近 130 行重复），
拆成 6 个文件后若继续复制就是 6 份 —— fixture/断言漂移会直接变成假绿，正是卡 04b 要收的那类雷。
conftest 是 pytest 为此提供的官方位置（同目录测试自动可见，无需 import fixtures）。

独立口径原则不变：这里的金额/数字断言都**不复用** `tools/` 的实现（避免自证）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from data import dao
from data.db import connect, transaction
from data.seed import SAVINGS_ID, generate
from tools import _wealth_risk, query, subscription, transfer

# ---------------- 测试数据常量（多个模块共用） ----------------

ACCOUNT = SAVINGS_ID
FOREIGN_USER, FOREIGN_ACCOUNT = "u_mallory_0002", "acc_aaa_foreign"   # id 更小 → DAO 会优先返回它
FOREIGN_TXN, FOREIGN_SUB, FOREIGN_CARD = "txn-x-0001", "sub_x_0001", "card_x_0001"
FOREIGN_AMOUNT = 999_900
FOREIGN_PAYEE = "payee_x_0001"
PAYEE = "payee_0001"        # 李四（白名单，有使用记录）
PAYEE_NEW = "payee_0002"    # 李四（非白名单 → 新收款人）
OTP = transfer.OTP_CODE


class Clock:
    """可控时钟：默认 2026-09-12 12:00（白天、无凌晨因子）。"""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def tick(self, **kwargs: float) -> datetime:
        self.moment += timedelta(**kwargs)
        return self.moment


# ---------------- fixtures ----------------


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """钉死 `transfer._now` / `subscription._now`（时间相关档位/窗口断言的确定性来源）。

    卡 06 追加订阅侧：取消的 `effective_date`、确认凭证的 TTL、僵尸订阅的「近 3 个月」窗口
    都读 `subscription._now`，不钉住就会随运行时刻抖动。
    """
    fixed = Clock(datetime(2026, 9, 12, 12, 0, 0))
    monkeypatch.setattr(transfer, "_now", fixed)
    monkeypatch.setattr(subscription, "_now", fixed)
    return fixed


@pytest.fixture()
def seeded(tmp_path: Path, clock: Clock) -> Path:
    """合成数据（600 流水 + 植入异常 + 订阅/收款人）建库并接上 DAO；顺带复位会话与 token 表。

    依赖 `clock`：转账侧的时间相关口径（今日累计、短时高频、凌晨因子）必须钉在固定时刻，
    否则断言会随运行时刻抖动（卡 04b 拆 conftest 时保留了这个依赖关系）。
    """
    path = tmp_path / "bank.db"
    generate(path)
    dao.connect_db(path)
    query.set_current_user(None)
    transfer.set_session_id("session-test")
    transfer._TOKENS.clear()
    subscription._CONFIRM_REFS.clear()      # 卡 06：确认凭证是进程内状态，必须逐用例复位
    _wealth_risk._ASSESSMENTS.clear()       # 卡 07：风险测评结果同族（进程内），同样逐用例复位
    yield path
    transfer._TOKENS.clear()
    subscription._CONFIRM_REFS.clear()
    _wealth_risk._ASSESSMENTS.clear()
    transfer.set_session_id(None)
    query.set_current_user(None)
    dao.close()


@pytest.fixture()
def blank(tmp_path: Path) -> Path:
    """空库（只建表、无数据）：空结果类边界用。"""
    path = tmp_path / "blank.db"
    dao.connect_db(path)
    query.set_current_user(None)
    yield path
    query.set_current_user(None)
    dao.close()


@pytest.fixture()
def foreign(seeded: Path) -> Path:
    """在张三的库上再插入一个用户（mallory）的账户/流水/卡/订阅：账户 id 排在最前，试探越权。"""
    conn = connect(seeded)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO user (id, name, phone, kyc_level) VALUES (?, ?, ?, ?)",
                         (FOREIGN_USER, "马洛里", "137****9002", "L1"))
            conn.execute("INSERT INTO account (id, user_id, type, balance, available, status)"
                         " VALUES (?, ?, 'savings', ?, ?, 'active')",
                         (FOREIGN_ACCOUNT, FOREIGN_USER, FOREIGN_AMOUNT, FOREIGN_AMOUNT))
            conn.execute("INSERT INTO txn (id, account_id, ts, amount, direction, counterparty,"
                         " category, channel, memo, balance_after) VALUES (?, ?, ?, ?, 'out', ?, ?, ?, ?, ?)",
                         (FOREIGN_TXN, FOREIGN_ACCOUNT, "2026-08-10T10:00:00", -FOREIGN_AMOUNT,
                          "他人的商户", "餐饮", "卡", "他人的备注", 0))
            conn.execute("INSERT INTO card (id, user_id, account_id, card_no_mask, type,"
                         " single_limit, daily_limit, status) VALUES (?, ?, ?, ?, 'savings', ?, ?, ?)",
                         (FOREIGN_CARD, FOREIGN_USER, FOREIGN_ACCOUNT, "6222 **** **** 9999", 1, 1, "normal"))
            conn.execute("INSERT INTO subscription (id, user_id, merchant, amount, cycle,"
                         " next_charge_date, status) VALUES (?, ?, ?, ?, 'monthly', ?, 'active')",
                         (FOREIGN_SUB, FOREIGN_USER, "他人的会员", 9_900, "2026-09-30"))
    finally:
        conn.close()
    return seeded


@pytest.fixture()
def foreign_payee(seeded: Path) -> Path:
    """插入第二个用户的收款人与账户（id 更小）：试探转账侧的越权与 fail-closed。"""
    conn = connect(seeded)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO user (id, name, phone, kyc_level) VALUES (?, ?, ?, 'L1')",
                         (FOREIGN_USER, "马洛里", "137****9002"))
            conn.execute("INSERT INTO account (id, user_id, type, balance, available, status)"
                         " VALUES ('acc_aaa_foreign', ?, 'savings', 999900, 999900, 'active')",
                         (FOREIGN_USER,))
            conn.execute("INSERT INTO payee (id, user_id, name, phone, bank, is_whitelist)"
                         " VALUES (?, ?, '李四', '139****9999', '他人银行', 1)",
                         (FOREIGN_PAYEE, FOREIGN_USER))
    finally:
        conn.close()
    return seeded


# ---------------- 独立口径的辅助（不经过 DAO、不经过工具层） ----------------


def raw(path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """独立直查（不经过 DAO、不经过工具层）。"""
    conn = connect(path)
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def write_sql(path: Path, statements: list[tuple[str, tuple]]) -> None:
    """独立写入（不走 DAO 的 `_writing()`，用于构造测试场景）。"""
    conn: sqlite3.Connection = connect(path)
    try:
        with transaction(conn):
            for sql, params in statements:
                conn.execute(sql, params)
    finally:
        conn.close()


def count(path: Path, table: str) -> int:
    return raw(path, f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]


def balance(path: Path) -> int:
    return raw(path, "SELECT balance FROM account WHERE id = ?", (SAVINGS_ID,))[0]["balance"]


def money(cents: int) -> str:
    """独立复算的“分 → 元”（纯整数），用于核对 facts 的展示串。"""
    whole, frac = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole:,}.{frac:02d}"


_DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?")
_NUMBER = re.compile(r"\d[\d,\.]*")


def numbers(text: str) -> set[str]:
    """文本里的数字，归一化为纯数字串（剥掉千分位/小数点/百分号/元；日期时间先摘掉）。"""
    return {re.sub(r"\D", "", token) for token in _NUMBER.findall(_DATEISH.sub(" ", text))}


def facts_numbers(facts: dict) -> set[str]:
    """facts 里所有数字（递归、含 *_yuan 展示串与嵌套结构）的归一化数字串。"""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found.add(str(abs(node)))
        elif isinstance(node, str):
            found.update(numbers(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(facts)
    return found


def assert_covered(text: str, facts: dict) -> None:
    """红线 1：text 里的每个数字都必须在 facts 里找得到。"""
    missing = numbers(text) - facts_numbers(facts)
    assert not missing, f"这些数字不在 facts 里（幻觉红线）：{sorted(missing)}｜文本：{text[:200]}"


def assert_dates_covered(text: str, facts: dict) -> None:
    """日期/时间 token 不计入数字校验，但必须能在 facts 里找到同形字符串。"""
    haystack = json.dumps(facts, ensure_ascii=False)
    for token in set(_DATEISH.findall(text)):
        assert token in haystack, f"文本里的日期 {token} 不在 facts 中"


def assert_no_floats(node: object, where: str = "root") -> None:
    """红线 2（严格版）：任何层级都不许出现 float / bool 冒充数字。"""
    if isinstance(node, bool):
        pytest.fail(f"{where} 出现 bool（金额必须整数分）")
    if isinstance(node, float):
        pytest.fail(f"{where} 出现浮点：{node!r}")
    if isinstance(node, dict):
        for key, value in node.items():
            assert_no_floats(value, f"{where}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            assert_no_floats(value, f"{where}[{index}]")


MONEY_KEYS = ("amount", "balance", "fee", "limit", "share", "mean", "_yuan", "cents")


def assert_no_money_floats(node: object, where: str = "root") -> None:
    """红线 2（宽松版）：不许 float；只有**金额相关字段**不许用 bool 冒充（`ambiguous` 等布尔字段合法）。"""
    if isinstance(node, float):
        pytest.fail(f"{where} 出现浮点：{node!r}")
    if isinstance(node, bool) and any(token in where.lower() for token in MONEY_KEYS):
        pytest.fail(f"{where} 用 bool 冒充金额")
    if isinstance(node, dict):
        for key, value in node.items():
            assert_no_money_floats(value, f"{where}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            assert_no_money_floats(value, f"{where}[{index}]")


def baseline_raw(path: Path, moment: str) -> int:
    """独立复算“该笔往前 90 天（左闭右开、不含本笔）支出均值”的整数地板值。

    口径里的 90 与 3 刻意**写成字面量**（来源：卡 04 第 3 条“近 90 天均值 3 倍”）：
    若复用被测常量，改坏阈值时测试会跟着变，等于自证。
    """
    floor = (datetime.fromisoformat(moment) - timedelta(days=90)).isoformat()
    outs = [-row["amount"] for row in raw(
        path, "SELECT amount FROM txn WHERE amount < 0 AND ts >= ? AND ts < ?", (floor, moment))]
    return sum(outs) // len(outs) if outs else 0


def txns_of(path: Path, where: str, params: tuple = ()) -> int:
    return raw(path, f"SELECT COUNT(*) AS n FROM txn WHERE {where}", params)[0]["n"]


def preview_ok(payee_id: str = PAYEE, amount: int = 10_000) -> transfer.ToolResult:
    result = transfer.preview_transfer(payee_id, amount)
    assert result.ok, result.message
    return result


def add_today_flows(path: Path, rows: list[tuple[str, int]]) -> None:
    """往今日塞入支出流水（单日累计/短时高频口径用）；rows = [(ts, amount), ...]。"""
    write_sql(path, [
        ("INSERT INTO txn (id, account_id, ts, amount, direction, counterparty, category, channel,"
         " memo, balance_after) VALUES (?, ?, ?, ?, 'out', '灌水商户', '餐饮', '二维码', NULL, 0)",
         (f"txn-fill-{index:03d}", SAVINGS_ID, ts, cents))
        for index, (ts, cents) in enumerate(rows)])
