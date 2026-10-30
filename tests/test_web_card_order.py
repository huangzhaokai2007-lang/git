"""业务卡渲染顺序/唯一性守卫（2026-10-30 起由 `panel_router` 统一裁决）。

契约：
1. 一屏**最多一张**业务卡（收款人 / 预约 / 群收款 / 卡片）；
2. 解析卡待选、或在途确认卡时，业务卡**一律让位**；
3. 确认卡之后不再出现业务卡（修改面板作为卡内抽屉渲染）。
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'interfaces/web'))
from interfaces.web import panel_router, workspace_ui          # noqa: E402

PANEL_MODULES = (('payee_card_ui', 'payee'), ('schedule_card_ui', 'schedule'),
                 ('aa_card_ui', 'aa'), ('gift_card_ui', 'gift'), ('card_binding_ui', 'cards'))


def _wire(monkeypatch, intent, calls: list, *, scene: str = 'scene') -> None:
    state = {'turns': [{'turn': {'intent': intent}}] if intent else []}
    if intent == 'card_query':                 # 卡片面板由 scene=='cards' 那段渲染
        state['bound-card-query'] = True
    monkeypatch.setattr(workspace_ui, 'st', SimpleNamespace(session_state=state))
    monkeypatch.setattr(workspace_ui, 'current', lambda: scene)
    for name in ('scope', 'error', 'result'):
        monkeypatch.setattr(workspace_ui, name, lambda *a, **kw: None)
    for module_name, label in (('activity_ui', 'activity'), ('payee_picker_ui', 'picker'),
                               ('card_edit_ui', 'edit'), *PANEL_MODULES,
                               ('scene_ui', 'selection'), ('slot_form_ui', 'slot-form')):
        monkeypatch.setattr(getattr(workspace_ui, module_name), 'render',
                            lambda *a, _label=label, **kw: calls.append(_label))


def test_only_the_panel_matching_the_last_turn_is_rendered(monkeypatch) -> None:
    """按最近一轮意图只画**一张**业务卡（其余业务卡一律不画）。"""
    for intent, expected in (('payee_list', 'payee'), ('scheduled_list', 'schedule'),
                             ('aa_list', 'aa'), ('gift_order_list', 'gift'),
                             ('card_query', 'cards'), ('balance_query', None)):
        calls: list = []
        _wire(monkeypatch, intent, calls, scene='cards' if intent == 'card_query' else 'scene')
        workspace_ui.panels(None, 'order-test', None,
                            lambda: calls.append('pending'), lambda: calls.append('forms'),
                            lambda: calls.append('confirm'))
        rendered = [name for name in calls if name in ('payee', 'schedule', 'aa', 'gift', 'cards')]
        if expected is None:
            assert rendered == [], f'{intent} 不该画业务卡：{rendered}'
        else:
            assert rendered == [expected], f'{intent} 应只画 {expected}，实际 {rendered}'
            if 'confirm' in calls:
                assert calls.index(expected) < calls.index('confirm')


def test_router_lets_the_picker_and_confirmation_win() -> None:
    """解析卡待选 / 在途确认时，业务卡让位（这是"乱出现"的主要来源）。"""
    assert panel_router.active_panel(intent='payee_list', has_confirmation=False,
                                     picker_pending=True) is None
    #: 群收款的确认卡**内嵌在业务卡里**（三卡合一）→ 有确认时仍要画那张卡；
    #: 一般的写意图（如转账）确认卡自带抽屉，业务卡让位。
    assert panel_router.active_panel(intent='aa_collect', has_confirmation=True,
                                     picker_pending=False) == 'aa'
    assert panel_router.active_panel(intent='transfer_single', has_confirmation=True,
                                     picker_pending=False) is None
    assert panel_router.active_panel(intent='aa_collect', has_confirmation=False,
                                     picker_pending=False) == 'aa'


def test_write_intents_map_to_their_home_panel() -> None:
    """按钮触发的写意图也归到它的面板（否则点完按钮场景会跳走 / 多画一张卡）。"""
    assert panel_router.INTENT_PANEL['payee_whitelist'] == 'payee'
    assert panel_router.INTENT_PANEL['scheduled_modify'] == 'schedule'
    assert panel_router.INTENT_PANEL['aa_mark_paid'] == 'aa'


def test_panels_are_decided_before_confirmation_in_source() -> None:
    """源码顺序：业务卡在确认卡之前（确认卡始终压在最下面贴近聊天框）。"""
    source = (ROOT / 'interfaces/web/workspace_ui.py').read_text(encoding='utf-8')
    panels_call = source.index('panel_router.active_panel(')
    confirmation_call = source.index('confirmation()', panels_call)
    assert panels_call < confirmation_call
    tree = ast.parse((ROOT / 'interfaces/web/app.py').read_text(encoding='utf-8'))
    page = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == '_chat_page')
    calls = {ast.unparse(node.func): node.lineno for node in ast.walk(page)
             if isinstance(node, ast.Call)}
    assert calls['workspace_ui.panels'] < calls['st.chat_input']
