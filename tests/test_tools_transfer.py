"""任务卡 05 单测：T6–T9（`tools/transfer.py`）。

三类用例：正常 / 边界（限额与档位边界、TTL 过期、整除与余数）/ 非法参数。另加几组红线断言：
1. **只算不执行**：`preview_transfer` 前后流水/审计行数必须一字不变。
2. **幂等**：同一 token 重复调用只扣一次款（余额与行数都不再变），且同一笔的两次结果逐字相同。
3. **事务原子性**：执行中途抛错 → 余额与流水**零残留**（同时证明没有嵌套 `BEGIN`，台账 R1）。
4. 事实包/禁浮点/错误消息无数字/OTP 不外泄/无 LLM 调用（静态断言）。

时间一律由 `clock` fixture 钉死（12:00 白天），避免"凌晨因子"让档位断言随运行时刻抖动。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from data import dao
from data.db import connect, transaction
from data.seed import SAVINGS_ID, USER_ID, generate
from tools import query, transfer

PAYEE = "payee_0001"        # 李四（白名单，有使用记录）
PAYEE_NEW = "payee_0002"    # 李四（非白名单 → 新收款人）
FOREIGN_USER, FOREIGN_PAYEE = "u_mallory_0002", "payee_x_0001"
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


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fixed = Clock(datetime(2026, 9, 12, 12, 0, 0))
    monkeypatch.setattr(transfer, "_now", fixed)
    return fixed


@pytest.fixture()
def seeded(tmp_path: Path, clock: Clock) -> Path:
    path = tmp_path / "bank.db"
    generate(path)
    dao.connect_db(path)
    query.set_current_user(None)
    transfer.set_session_id("session-test")
    transfer._TOKENS.clear()
    yield path
    transfer._TOKENS.clear()
    transfer.set_session_id(None)
    query.set_current_user(None)
    dao.close()


@pytest.fixture()
def foreign(seeded: Path) -> Path:
    """插入第二个用户的收款人与账户：试探越权与 fail-closed。"""
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


# ---------------- 独立口径辅助 ----------------

def raw(path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = connect(path)
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def write_sql(path: Path, statements: list[tuple[str, tuple]]) -> None:
    conn = connect(path)
    try:
        with transaction(conn):
            for sql, params in statements:
                conn.execute(sql, params)
    finally:
        conn.close()


def count(path: Path, table: str) -> int:
    return raw(path, f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]


def money(cents: int) -> str:
    whole, frac = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole:,}.{frac:02d}"


def balance(path: Path) -> int:
    return raw(path, "SELECT balance FROM account WHERE id = ?", (SAVINGS_ID,))[0]["balance"]


_DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?")
_NUMBER = re.compile(r"\d[\d,\.]*")


def numbers(text: str) -> set[str]:
    return {re.sub(r"\D", "", token) for token in _NUMBER.findall(_DATEISH.sub(" ", text))}


def facts_numbers(facts: dict) -> set[str]:
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
    missing = numbers(text) - facts_numbers(facts)
    assert not missing, f"这些数字不在 facts 里（幻觉红线）：{sorted(missing)}｜文本：{text[:200]}"


MONEY_KEYS = ("amount", "balance", "fee", "limit", "share", "mean", "_yuan", "cents")


def assert_no_floats(node: object, where: str = "root") -> None:
    """禁浮点：任何层级都不许出现 float；金额相关字段还不许用 bool 冒充（T6 的 ambiguous 等布尔字段不算）。"""
    if isinstance(node, float):
        pytest.fail(f"{where} 出现浮点：{node!r}")
    if isinstance(node, bool) and any(token in where.lower() for token in MONEY_KEYS):
        pytest.fail(f"{where} 用 bool 冒充金额")
    if isinstance(node, dict):
        for key, value in node.items():
            assert_no_floats(value, f"{where}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            assert_no_floats(value, f"{where}[{index}]")


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


# ---------------- 接口冻结与枚举 ----------------

def test_error_code_enum_has_all_ten_registered_values() -> None:
    assert sorted(code.value for code in transfer.ErrorCode) == [
        "AMBIGUOUS", "FORBIDDEN", "HALLUCINATION_BLOCKED", "INSUFFICIENT_FUNDS", "INVALID_ARGUMENT",
        "INVALID_STATE", "NOT_FOUND", "OVER_LIMIT", "TOKEN_EXPIRED", "TOO_MANY_ROWS"]


def test_data_keys_are_frozen_to_the_spec(seeded: Path) -> None:
    assert set(transfer.resolve_payee("李四").data) == {"candidates", "ambiguous"}
    assert set(preview_ok().data) == {"preview_token", "fee", "tier", "requires_otp", "limits"}
    executed = transfer.execute_transfer(preview_ok().data["preview_token"])
    assert set(executed.data) == {"txn_id", "amount", "payee_name", "balance_after"}
    assert set(transfer.create_aa_request([PAYEE], 1_000).data) == {"request_id", "per_person_amount"}


def test_module_never_calls_an_llm() -> None:
    """铁律：execute 里禁止调 LLM —— 静态断言本模块不引用任何模型客户端。"""
    source = Path(transfer.__file__).read_text(encoding="utf-8")
    assert not re.search(r"import\s+(openai|llm)|from\s+(openai|agent)|deepseek", source, re.I)


# ---------------- T6 resolve_payee ----------------

def test_resolve_payee_reports_ambiguity_for_same_name(seeded: Path) -> None:
    result = transfer.resolve_payee("李四")
    assert result.ok and result.data["ambiguous"] is True
    assert len(result.data["candidates"]) == 2
    assert {item["name"] for item in result.data["candidates"]} == {"李四"}
    assert len({item["masked_phone"] for item in result.data["candidates"]}) == 2
    assert result.facts["candidate_count"] == 2 and result.facts["ambiguous"] is True
    assert_covered(result.message, result.facts)


def test_resolve_payee_single_match_includes_phone_mask(seeded: Path) -> None:
    result = transfer.resolve_payee("赵六")
    assert result.ok and result.data["ambiguous"] is False
    assert result.data["candidates"] == [{"id": "payee_0005", "name": "赵六", "masked_phone": "135****4005"}]
    assert_covered(result.message, result.facts)


def test_resolve_payee_matches_phone_fragment(seeded: Path) -> None:
    result = transfer.resolve_payee("139")
    assert {item["id"] for item in result.data["candidates"]} == {"payee_0001", "payee_0002"}


def test_resolve_payee_no_match_is_empty_not_an_error(seeded: Path) -> None:
    result = transfer.resolve_payee("查无此人")
    assert result.ok and result.data == {"candidates": [], "ambiguous": False}
    assert result.facts["candidate_count"] == 0 and not re.search(r"\d", result.message)


def test_resolve_payee_hides_other_users_payees(foreign: Path) -> None:
    result = transfer.resolve_payee("李四")
    assert {item["id"] for item in result.data["candidates"]} == {"payee_0001", "payee_0002"}
    assert FOREIGN_PAYEE not in {item["id"] for item in result.data["candidates"]}


@pytest.mark.parametrize("bad", ["", "   ", None, 139, ["李四"]])
def test_resolve_payee_rejects_illegal_query(seeded: Path, bad: object) -> None:
    result = transfer.resolve_payee(bad)                        # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


# ---------------- T7 preview_transfer ----------------

def test_preview_whitelist_within_limit_is_l1_without_otp(seeded: Path) -> None:
    result = preview_ok(PAYEE, 10_000)
    assert result.data["tier"] == "L1" and result.data["requires_otp"] is False
    assert result.data["fee"] == 0 and result.facts["factors"] == [] and result.facts["escalation"] == 0
    assert result.facts["ttl_seconds"] == 300
    assert result.data["preview_token"].startswith("pt_")
    assert result.data["preview_token"] not in json.dumps(result.facts)      # token 不进事实包
    assert_covered(result.message, result.facts)


def test_preview_non_whitelist_is_l2_and_new_payee_is_not_double_counted(seeded: Path) -> None:
    """规格 §5 的 L2 基础条件就是「新收款人」：它不能再把档位升到 L3（否则 L2+OTP 永远走不到）。"""
    result = preview_ok(PAYEE_NEW, 5_000)
    assert result.data["tier"] == "L2" and result.data["requires_otp"] is True
    assert result.facts["factors"] == ["new_payee"] and result.facts["escalation"] == 0
    assert result.facts["to_human"] is False


def test_preview_night_factor_escalates_one_tier(seeded: Path, clock: Clock) -> None:
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)
    result = preview_ok(PAYEE, 10_000)
    assert result.data["tier"] == "L2" and result.facts["factors"] == ["night"]
    clock.moment = datetime(2026, 9, 12, 23, 0, 0)              # 边界：23:00 命中
    assert preview_ok(PAYEE, 10_000).facts["factors"] == ["night"]
    clock.moment = datetime(2026, 9, 12, 6, 0, 0)               # 边界：06:00 不命中
    assert preview_ok(PAYEE, 10_000).facts["factors"] == []


def test_preview_velocity_needs_three_writes_within_ten_minutes(seeded: Path) -> None:
    """短时高频：含本次在内 10 分钟内 ≥3 笔写操作才命中（前两笔不命中）。"""
    assert preview_ok(PAYEE, 1_000).facts["factors"] == []
    transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"])
    assert preview_ok(PAYEE, 1_000).facts["factors"] == []       # 已有 1 笔 + 本次 = 2
    transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"])
    third = preview_ok(PAYEE, 1_000)                             # 已有 2 笔 + 本次 = 3 → 命中
    assert third.facts["factors"] == ["velocity"] and third.data["tier"] == "L2"


def test_preview_amount_jump_factor_uses_five_times_history(seeded: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """金额偏离（写操作降级因子）= 规格 §5 的 **5 倍**，与卡 04 T4 的只读检测 3 倍不是一回事。

    正常账户里 5 倍历史均值(~1,000元) 已被"单笔上限 500元"遮蔽，故这里把历史均值压小来钉住因子逻辑。
    """
    assert transfer.AMOUNT_JUMP_RATIO == 5 and transfer.HISTORY_DAYS == 90
    monkeypatch.setattr(transfer, "_history_mean_cents", lambda: (100, 50))
    assert preview_ok(PAYEE, 501).facts["factors"] == ["amount_jump"]
    assert preview_ok(PAYEE, 500).facts["factors"] == []         # 恰好 5 倍不算（严格大于）


def test_preview_night_plus_velocity_goes_to_human(seeded: Path, clock: Clock) -> None:
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)               # 凌晨：先要过 OTP 才能落成写操作
    for _ in range(2):
        transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"], otp=OTP)
    result = preview_ok(PAYEE, 1_000)
    assert result.data["tier"] == "L3" and result.facts["to_human"] is True
    assert set(result.facts["factors"]) == {"night", "velocity"}


def test_preview_writes_absolutely_nothing(seeded: Path) -> None:
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    preview_ok(PAYEE, 10_000)
    preview_ok(PAYEE_NEW, 5_000)
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == before


def test_preview_reports_remaining_limits_from_today(seeded: Path) -> None:
    result = preview_ok(PAYEE, 10_000)
    assert result.data["limits"] == {"single_max": 50_000, "daily_max": 200_000,
                                     "daily_used": 0, "daily_remaining": 200_000}
    assert result.facts["limits_daily_remaining_yuan"] == "2,000.00"
    add_today_flows(seeded, [("2026-09-12T09:00:00", -160_000)])
    used = preview_ok(PAYEE, 30_000)
    assert used.facts["limits_daily_used"] == 160_000 and used.data["limits"]["daily_remaining"] == 40_000


def test_preview_enforces_single_and_daily_hard_limits(seeded: Path) -> None:
    over_single = transfer.preview_transfer(PAYEE, 50_001)
    assert over_single.error_code == "OVER_LIMIT" and over_single.data is None
    assert transfer.preview_transfer(PAYEE, 50_000).ok                       # 恰好上限：放行
    add_today_flows(seeded, [("2026-09-12T09:00:00", -160_000)])
    assert transfer.preview_transfer(PAYEE, 50_000).error_code == "OVER_LIMIT"      # 160000+50000 > 200000
    assert preview_ok(PAYEE, 40_000).facts["limits_daily_used"] == 160_000         # 恰好不超：放行


def test_preview_reports_insufficient_funds(seeded: Path) -> None:
    write_sql(seeded, [("UPDATE account SET balance = 100, available = 100 WHERE id = ?", (SAVINGS_ID,))])
    result = transfer.preview_transfer(PAYEE, 5_000)
    assert result.ok is False and result.error_code == "INSUFFICIENT_FUNDS"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)
    assert transfer.preview_transfer(PAYEE, 100).ok                          # 恰好够：放行


def test_preview_rejects_unknown_or_foreign_payee(seeded: Path, foreign: Path) -> None:
    assert transfer.preview_transfer("payee_nope", 100).error_code == "NOT_FOUND"
    refused = transfer.preview_transfer(FOREIGN_PAYEE, 100)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}


def test_preview_refuses_unimplemented_schedule_and_split(seeded: Path) -> None:
    assert transfer.preview_transfer(PAYEE, 100, schedule="2026-10-01").error_code == "INVALID_ARGUMENT"
    assert transfer.preview_transfer(PAYEE, 100, split_with=[PAYEE_NEW]).error_code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("args,kwargs", [
    (("", 100), {}), (("payee_0001", 0), {}), (("payee_0001", -100), {}),
    (("payee_0001", 1.5), {}), (("payee_0001", True), {}), (("payee_0001", "100"), {}),
    (("payee_0001", 100), {"schedule": 20261001}),
])
def test_preview_rejects_illegal_arguments(seeded: Path, args: tuple, kwargs: dict) -> None:
    result = transfer.preview_transfer(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


# ---------------- T8 execute_transfer ----------------

def test_execute_debits_once_and_writes_txn_plus_audit(seeded: Path) -> None:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    before, start = (count(seeded, "txn"), count(seeded, "audit_log")), balance(seeded)
    result = transfer.execute_transfer(token)
    assert result.ok and result.data["balance_after"] == start - 10_000 == balance(seeded)
    assert result.data["amount"] == 10_000 and result.data["payee_name"] == "李四"
    row = raw(seeded, "SELECT * FROM txn WHERE id = ?", (result.data["txn_id"],))[0]
    assert (row["amount"], row["direction"], row["category"], row["counterparty"]) == \
        (-10_000, "out", "转账", "李四")
    assert (count(seeded, "txn"), count(seeded, "audit_log")) == (before[0] + 1, before[1] + 1)
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert (audit["tool"], audit["intent"], audit["result"], audit["permission_tier"]) == \
        ("execute_transfer", "transfer_single", "success", "L1")
    assert audit["session_id"] == "session-test" and audit["trace_id"].startswith("trace-")
    assert_covered(result.message, result.facts)


def test_execute_twice_with_the_same_token_debits_only_once(seeded: Path) -> None:
    """卡 05 点名用例：重复调用同一 token 只扣一次款。"""
    token = preview_ok(PAYEE, 20_000).data["preview_token"]
    start, txn_count = balance(seeded), count(seeded, "txn")
    first = transfer.execute_transfer(token)
    after_first = balance(seeded)
    second = transfer.execute_transfer(token)
    assert first.data == second.data and first.facts == second.facts
    assert balance(seeded) == after_first == start - 20_000
    assert count(seeded, "txn") == txn_count + 1
    assert len(raw(seeded, "SELECT id FROM txn WHERE id = ?", (first.data["txn_id"],))) == 1


def test_two_tokens_for_the_same_transfer_move_money_twice(seeded: Path) -> None:
    """幂等键是 token，不是"收款人+金额"：两次独立预览 = 两笔真实转账。"""
    first = transfer.execute_transfer(preview_ok(PAYEE, 5_000).data["preview_token"])
    second = transfer.execute_transfer(preview_ok(PAYEE, 5_000).data["preview_token"])
    assert first.data["txn_id"] != second.data["txn_id"]
    assert first.data["balance_after"] == second.data["balance_after"] + 5_000


def test_execute_requires_the_right_otp_for_l2(seeded: Path) -> None:
    token = preview_ok(PAYEE_NEW, 5_000).data["preview_token"]
    before = (count(seeded, "txn"), balance(seeded))
    missing = transfer.execute_transfer(token)
    wrong = transfer.execute_transfer(token, otp="000000")
    assert missing.error_code == wrong.error_code == "FORBIDDEN"
    assert (count(seeded, "txn"), balance(seeded)) == before
    ok = transfer.execute_transfer(token, otp=OTP)
    assert ok.ok and count(seeded, "txn") == before[0] + 1
    assert transfer.execute_transfer(token, otp=OTP).data == ok.data       # 通过后再重放仍幂等


def test_execute_rejects_expired_token(seeded: Path, clock: Clock) -> None:
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    clock.tick(seconds=transfer.PREVIEW_TTL_SECONDS + 1)
    before = (count(seeded, "txn"), balance(seeded))
    result = transfer.execute_transfer(token)
    assert result.error_code == "TOKEN_EXPIRED" and result.facts == {}
    assert (count(seeded, "txn"), balance(seeded)) == before
    assert transfer.execute_transfer(token).error_code == "TOKEN_EXPIRED"   # 过期即被摘除


def test_execute_unknown_token_is_token_expired(seeded: Path) -> None:
    result = transfer.execute_transfer("pt_does_not_exist")
    assert result.ok is False and result.error_code == "TOKEN_EXPIRED" and not re.search(r"\d", result.message)


def test_execute_refuses_a_token_of_another_user(seeded: Path) -> None:
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    query.set_current_user("u_someone_else")
    result = transfer.execute_transfer(token)
    assert result.error_code == "FORBIDDEN" and result.facts == {}
    assert transfer.preview_transfer(PAYEE, 1_000).error_code == "FORBIDDEN"      # 收款人也不归属


def test_execute_refuses_l3_instead_of_auto_executing(seeded: Path, clock: Clock) -> None:
    """L3 = 人工复核/延迟生效（规格 §4 PENDING_REVIEW）：本卡不自动执行，交编排层。"""
    clock.moment = datetime(2026, 9, 12, 2, 30, 0)
    for _ in range(2):
        transfer.execute_transfer(preview_ok(PAYEE, 1_000).data["preview_token"], otp=OTP)
    token = preview_ok(PAYEE, 1_000).data["preview_token"]
    assert transfer._TOKENS[token]["tier"] == "L3"               # 先确认这一版确实是 L3
    before = (count(seeded, "txn"), balance(seeded))
    result = transfer.execute_transfer(token, otp=OTP)
    assert result.error_code == "INVALID_STATE" and result.data is None
    assert (count(seeded, "txn"), balance(seeded)) == before


def test_execute_is_atomic_when_a_step_fails(seeded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写审计时抛错 → 扣款与流水一并回滚；且不抛"嵌套 BEGIN"（台账 R1 的正面证据）。"""
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    original = dao.insert_audit

    def boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("审计写入失败")

    monkeypatch.setattr(dao, "insert_audit", boom)
    with pytest.raises(RuntimeError):
        transfer.execute_transfer(token)
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == before
    monkeypatch.setattr(dao, "insert_audit", original)          # 只还原这一处，时钟补丁要留着
    assert transfer._TOKENS[token]["state"] == "preview"        # 未被消费 → 仍可重试
    assert transfer.execute_transfer(token).ok


def test_execute_rechecks_the_balance_after_preview(seeded: Path) -> None:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    write_sql(seeded, [("UPDATE account SET balance = 1, available = 1 WHERE id = ?", (SAVINGS_ID,))])
    result = transfer.execute_transfer(token)
    assert result.error_code == "INSUFFICIENT_FUNDS" and balance(seeded) == 1


def test_execute_never_leaks_the_otp(seeded: Path) -> None:
    token = preview_ok(PAYEE_NEW, 1_000).data["preview_token"]
    result = transfer.execute_transfer(token, otp=OTP)
    blob = json.dumps({"data": result.data, "facts": result.facts, "message": result.message}, ensure_ascii=False)
    assert OTP not in blob and OTP not in json.dumps(transfer._TOKENS[token], default=str)


@pytest.mark.parametrize("token", ["", None, 123, []])
def test_execute_rejects_illegal_arguments(seeded: Path, token: object) -> None:
    result = transfer.execute_transfer(token)                   # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"


# ---------------- T9 create_aa_request ----------------

def test_aa_split_puts_the_remainder_on_the_initiator(seeded: Path) -> None:
    result = transfer.create_aa_request([PAYEE, PAYEE_NEW, "payee_0003"], 10_000)
    assert result.ok and result.data["per_person_amount"] == 3_333
    assert result.facts["initiator_share"] == 1 and result.facts["total_check"] == 10_000
    assert 3_333 * 3 + 1 == 10_000
    assert result.data["request_id"].startswith("aa_")
    assert_covered(result.message, result.facts)


def test_aa_exact_division_has_no_remainder(seeded: Path) -> None:
    ids = ["payee_0001", "payee_0002", "payee_0003", "payee_0004", "payee_0005"]
    result = transfer.create_aa_request(ids, 10_000)
    assert result.data["per_person_amount"] == 2_000 and result.facts["initiator_share"] == 0
    assert result.facts["total_check"] == 10_000


def test_aa_single_payee_takes_the_whole_amount(seeded: Path) -> None:
    result = transfer.create_aa_request([PAYEE], 9_999)
    assert result.data["per_person_amount"] == 9_999 and result.facts["initiator_share"] == 0


def test_aa_amount_smaller_than_people_is_allowed_and_documented(seeded: Path) -> None:
    """规格未设"金额 ≥ 人数"下限，本卡不自行加限制：均摊得 0 分、余数全给发起人，合计仍精确相等。"""
    result = transfer.create_aa_request([PAYEE, PAYEE_NEW, "payee_0003"], 2)
    assert result.data["per_person_amount"] == 0 and result.facts["initiator_share"] == 2
    assert result.facts["total_check"] == 2


def test_aa_writes_only_an_audit_row(seeded: Path) -> None:
    before = (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded))
    result = transfer.create_aa_request([PAYEE], 1_000)
    assert result.ok
    assert (count(seeded, "txn"), count(seeded, "audit_log"), balance(seeded)) == \
        (before[0], before[1] + 1, before[2])
    audit = raw(seeded, "SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1")[0]
    assert (audit["tool"], audit["intent"], audit["result"]) == ("create_aa_request", "aa_collect", "success")


def test_aa_rejects_duplicates_and_unknown_or_foreign_payees(seeded: Path, foreign: Path) -> None:
    assert transfer.create_aa_request([PAYEE, PAYEE], 1_000).error_code == "INVALID_ARGUMENT"
    assert transfer.create_aa_request([PAYEE, "payee_nope"], 1_000).error_code == "NOT_FOUND"
    refused = transfer.create_aa_request([PAYEE, FOREIGN_PAYEE], 1_000)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}


@pytest.mark.parametrize("payee_ids,amount", [
    ([], 1_000), ([""], 1_000), ([PAYEE], 0), ([PAYEE], -1), ([PAYEE], 1.5), ([PAYEE], True),
    (PAYEE, 1_000), (None, 1_000), ([PAYEE], "1000"),
])
def test_aa_rejects_illegal_arguments(seeded: Path, payee_ids: object, amount: object) -> None:
    result = transfer.create_aa_request(payee_ids, amount)      # type: ignore[arg-type]
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {} and not re.search(r"\d", result.message)


# ---------------- 红线：与卡 04 同款的全局面 ----------------

def happy_calls(seeded: Path, clock: Clock) -> list[tuple[str, transfer.ToolResult]]:
    token = preview_ok(PAYEE, 10_000).data["preview_token"]
    return [("resolve_payee", transfer.resolve_payee("李四")),
            ("preview_transfer", preview_ok(PAYEE, 10_000)),
            ("execute_transfer", transfer.execute_transfer(token)),
            ("create_aa_request", transfer.create_aa_request([PAYEE, PAYEE_NEW], 10_000))]


def test_reply_messages_numbers_are_all_in_facts(seeded: Path, clock: Clock) -> None:
    for name, result in happy_calls(seeded, clock):
        assert result.ok, name
        assert_covered(result.message, result.facts)


def test_no_floats_anywhere_in_data_or_facts(seeded: Path, clock: Clock) -> None:
    for name, result in happy_calls(seeded, clock):
        assert_no_floats(result.data, f"{name}.data")
        assert_no_floats(result.facts, f"{name}.facts")
        json.dumps({"data": result.data, "facts": result.facts}, ensure_ascii=False)


def test_money_and_ownership_helpers_agree_with_query_module(seeded: Path) -> None:
    """两处同口径实现（重复实现，待 04b/05b 合并）必须给出相同结果，否则就是漂移。"""
    for cents in (0, 5, 100, -123_456, 9_999_999):
        assert transfer._money(cents) == query._money(cents)
    assert transfer._owned_account_ids() == query._owned_account_ids() == {SAVINGS_ID, "acc_credit_0001"}


ILLEGAL_CALLS = [
    ("resolve_payee", ("",), {}),
    ("preview_transfer", ("", 100), {}),
    ("preview_transfer", (PAYEE, 0), {}),
    ("execute_transfer", ("",), {}),
    ("create_aa_request", ([], 100), {}),
]


@pytest.mark.parametrize("name,args,kwargs", ILLEGAL_CALLS, ids=[f"{c[0]}-{i}" for i, c in enumerate(ILLEGAL_CALLS)])
def test_rejected_calls_return_a_clean_digit_free_result(seeded: Path, name: str, args: tuple,
                                                         kwargs: dict) -> None:
    result = getattr(transfer, name)(*args, **kwargs)
    assert result.ok is False and result.error_code == "INVALID_ARGUMENT"
    assert result.data is None and result.facts == {}
    assert result.message and not re.search(r"\d", result.message)


def test_foreign_account_does_not_pay_for_our_transfers(foreign: Path) -> None:
    """他人账户 id 更小 → 本人储蓄账户取不到 → fail-closed：拒绝转账，绝不从他人账户扣款。"""
    refused = transfer.preview_transfer(PAYEE, 1_000)
    assert refused.error_code == "FORBIDDEN" and refused.facts == {}
    foreign_balance = raw(foreign, "SELECT balance, available FROM account WHERE id = 'acc_aaa_foreign'")[0]
    assert (foreign_balance["balance"], foreign_balance["available"]) == (999_900, 999_900)   # 分毫未动
