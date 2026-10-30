"""面板切换时子状态要复位（用户 2026-10-30：卡片展开态/编辑态残留）。

- 换业务卡 → 离场那张卡的展开态（`payee-open` / `sched-edit` / `aa-create-open` …）清掉；
- 确认卡消失 → 「修改」抽屉的展开态复位（否则下一张卡会自动展开）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'interfaces/web'))
from interfaces.web import panel_router                          # noqa: E402


def test_leaving_and_entering_panels_resets_their_expand_state() -> None:
    """群收款 → 收款人：群收款的展开态要复位；入场那张卡的展开态也复位。"""
    reset = panel_router.clears_state('aa', 'payee')
    assert set(reset) == {'aa-create-open', 'aa-pick-open', 'aa-split-open', 'payee-open'}
    assert all(value is None for value in reset.values())


def test_schedule_edit_state_is_reset_on_switch() -> None:
    """预约 → 别处：行内编辑（`sched-edit`）不能带到下一张卡。"""
    assert 'sched-edit' in panel_router.clears_state('schedule', 'aa')


def test_no_reset_when_panel_unchanged() -> None:
    """同一张卡内部操作（子状态变化）不该被复位。"""
    assert panel_router.clears_state('aa', 'aa') == {
        'aa-create-open': None, 'aa-pick-open': None, 'aa-split-open': None}
    assert panel_router.clears_state(None, None) == {}
