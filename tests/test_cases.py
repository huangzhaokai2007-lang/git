"""卡 11：用例驱动回归（规格 §8 的 `tests/cases/*.yaml`）。

用例是**数据 + 断言**，不是新实现：每条用例给一句用户输入（+ 假 LLM 的槽位），驱动现有的
`orchestrator.handle`（卡 09/10 的状态机与写路径）或直接驱动工具层（越权/伪造这类编排层尚未路由的边界），
然后逐项比对 intent / tool_calls / tier / must_contain / must_not_contain / executed 等字段。

通过率：`test_case_pass_rate` 会打印真实通过率（`python -m pytest tests/test_cases.py -s` 可见）；
任一用例失败 → 该用例的断言打印「期望 vs 实际」逐条 diff；整体失败 → pytest 退出码非 0（verify.sh 第 3 段据此判红）。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from agent import confirm_card, llm, orchestrator, write_flow
from tools import subscription

CASES_DIR = Path(__file__).parent / "cases"

#: 规格 §8 / 卡 11 的分组下限（账单 5 / 转账 6 / 订阅 5 / 卡片 4 / 理财 4 / 越权与安全 6）
REQUIRED_GROUPS = {"bill": 5, "trf": 6, "sub": 5, "card": 4, "wlt": 4, "sec": 6}

TOOL_MODULES = {"subscription": subscription}


def load_cases() -> list[dict]:
    """读取全部 `tests/cases/*.yaml`（每条一个 dict）。"""
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for case in loaded:
            case["_file"] = path.name
            cases.append(case)
    return cases


CASES = load_cases()
CASE_IDS = [case["id"] for case in CASES]


def _fake_llm(monkeypatch: pytest.MonkeyPatch, verdict: dict) -> None:
    """假 LLM：分类固定返回用例声明的结果；润色原样返回（数字不动）。"""

    def chat_json(system: str, user: str, schema):
        if "润色" in system:
            return schema(reply=__import__("json").loads(user)["reply"])
        return schema.model_validate(verdict)

    monkeypatch.setattr(llm, "chat_json", chat_json)


def _effective_tier(turn, intent: str) -> str | None:
    """编排层只对写路径回填 tier；只读意图恒 L0（§5 表）。"""
    return turn.tier or ("L0" if intent in orchestrator.READ_INTENTS else None)


def _diffs_for_turn(case: dict, session: str, turn) -> list[str]:
    expect, diffs = case.get("expect") or {}, []
    intent = expect.get("intent", turn.intent)
    if "intent" in expect and turn.intent != expect["intent"]:
        diffs.append(f"intent      期望 {expect['intent']!r} / 实际 {turn.intent!r}")
    if "tool_calls" in expect and turn.tool_calls != list(expect["tool_calls"]):
        diffs.append(f"tool_calls  期望 {list(expect['tool_calls'])} / 实际 {turn.tool_calls}")
    if "tier" in expect and _effective_tier(turn, intent) != expect["tier"]:
        diffs.append(f"tier        期望 {expect['tier']!r} / 实际 {_effective_tier(turn, intent)!r}")
    if "executed" in expect and turn.executed is not expect["executed"]:
        diffs.append(f"executed    期望 {expect['executed']} / 实际 {turn.executed}")
    if "to_human" in expect and turn.to_human is not expect["to_human"]:
        diffs.append(f"to_human    期望 {expect['to_human']} / 实际 {turn.to_human}")
    if "requires_otp" in expect:
        inflight = confirm_card.current(session)
        actual = None if inflight is None else inflight.requires_otp
        if actual is not expect["requires_otp"]:
            diffs.append(f"requires_otp 期望 {expect['requires_otp']} / 实际 {actual}")
    for token in expect.get("must_contain") or []:
        if token not in turn.reply:
            diffs.append(f"must_contain 缺少 {token!r} / 实际回执 {turn.reply!r}")
    for token in expect.get("must_not_contain") or []:
        if token in turn.reply:
            diffs.append(f"must_not_contain 出现了 {token!r} / 实际回执 {turn.reply!r}")
    return diffs


def run_case(case: dict, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """跑一条用例，返回 diff 列表（空 = 通过）。"""
    expect = case.get("expect") or {}
    if "tool" in case:                                                 # 工具层用例
        spec = case["tool"]
        module, func = spec["name"].split(".")
        result = getattr(TOOL_MODULES[module], func)(**spec["args"])
        diffs = []
        if expect.get("error_code") and write_flow.error_code_of(result) != expect["error_code"]:
            diffs.append(f"error_code  期望 {expect['error_code']!r} / 实际 "
                         f"{write_flow.error_code_of(result)!r}（{result.message}）")
        for token in expect.get("detail_contains") or []:
            if token not in result.message:
                diffs.append(f"detail_contains 缺少 {token!r} / 实际 {result.message!r}")
        return diffs
    session = f"case-{case['id']}"                                     # 编排层用例（多轮）
    verdict = {"intent": expect.get("intent", "out_of_scope"), "confidence": 0.95,
               "slots": case.get("slots") or {}, "missing_slots": [], "unsafe_reason": "疑似注入指令"}
    _fake_llm(monkeypatch, verdict)
    turn = None
    for text in [case["input"], *(case.get("turns") or [])]:
        turn = orchestrator.handle(text, session_id=session)
    return _diffs_for_turn(case, session, turn)


@pytest.fixture()
def case_env(seeded: Path, clock, monkeypatch: pytest.MonkeyPatch):
    """每条用例都从干净的会话态与可控时钟开始。"""
    monkeypatch.setattr(confirm_card, "_now", clock)
    confirm_card.reset_state()
    yield clock
    confirm_card.reset_state()


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case(case_env, monkeypatch: pytest.MonkeyPatch, case: dict) -> None:
    if "now" in case:
        hour, minute = (int(part) for part in str(case["now"]).split(":"))
        case_env.moment = case_env.moment.replace(hour=hour, minute=minute)
    diffs = run_case(case, monkeypatch)
    assert not diffs, f"{case['id']}（{case['_file']}）失败：\n  " + "\n  ".join(diffs)


def test_case_manifest_meets_required_groups() -> None:
    """用例集结构：总数 ≥30 且各组数量不低于卡 11 的要求。"""
    counts = Counter(case["id"].split("-")[0] for case in CASES)
    assert len(CASES) >= 30, f"用例总数不足：{len(CASES)}"
    for group, minimum in REQUIRED_GROUPS.items():
        assert counts[group] >= minimum, f"{group} 组用例不足：{counts[group]} < {minimum}"
    assert len(CASE_IDS) == len(set(CASE_IDS)), "用例 id 有重复"


def test_case_pass_rate(case_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """全量用例的真实通过率（打印出来；不达标即断言失败）。"""
    failures: list[str] = []
    for case in CASES:
        moment = case_env.moment                                        # 用例间恢复时钟，避免串味
        if "now" in case:
            hour, minute = (int(part) for part in str(case["now"]).split(":"))
            case_env.moment = case_env.moment.replace(hour=hour, minute=minute)
        confirm_card.reset_state()
        diffs = run_case(case, monkeypatch)
        if diffs:
            failures.append(f"{case['id']}: " + "；".join(diffs))
        confirm_card.reset_state()
        case_env.moment = moment
    passed = len(CASES) - len(failures)
    rate = passed / len(CASES) * 100 if CASES else 0.0
    print(f"\n用例通过率: {passed}/{len(CASES)}（{rate:.1f}%）")
    assert not failures, "未通过用例：\n  " + "\n  ".join(failures)
