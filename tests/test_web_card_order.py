"""The active business panel precedes confirmation, then the chat input."""
from pathlib import Path
from types import SimpleNamespace
import ast
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'interfaces/web'))
from interfaces.web import workspace_ui


@pytest.mark.parametrize(('scene', 'expected'), [
    ('payee_list', 'payee'), ('scheduled_list', 'schedule'),
    ('aa_list', 'aa'), ('gift_order_list', 'gift'), ('cards', 'cards'),
])
def test_active_panel_is_the_only_query_panel_and_precedes_confirmation(monkeypatch, scene, expected):
    calls = []
    state = {f'bound-{domain}-query': True for domain in ('payee', 'schedule', 'aa', 'gift', 'card')}
    monkeypatch.setattr(workspace_ui, 'st', SimpleNamespace(session_state=state))
    monkeypatch.setattr(workspace_ui, 'current', lambda: scene)
    for name in ('scope', 'error', 'result'):
        monkeypatch.setattr(workspace_ui, name, lambda *a, **kw: None)
    modules = (('activity_ui', 'activity'), ('payee_picker_ui', 'picker'), ('card_edit_ui', 'edit'),
               ('card_binding_ui', 'cards'), ('payee_card_ui', 'payee'),
               ('schedule_card_ui', 'schedule'), ('aa_card_ui', 'aa'), ('gift_card_ui', 'gift'),
               ('scene_ui', 'selection'), ('slot_form_ui', 'slot-form'))
    for name, label in modules:
        monkeypatch.setattr(getattr(workspace_ui, name), 'render',
                            lambda *a, _label=label, **kw: calls.append(_label))
    state['turns'] = []
    workspace_ui.panels(None, 'order-test', None,
                        lambda: calls.append('pending'), lambda: calls.append('forms'),
                        lambda: calls.append('confirm'))
    assert calls[-1] == 'confirm'
    assert calls.index('picker') < calls.index('forms') < calls.index(expected) < calls.index('confirm')
    assert [name for name in calls if name in ('cards', 'payee', 'schedule', 'aa', 'gift')] == [expected]


def test_business_panel_completes_before_chat_input():
    tree = ast.parse((ROOT / 'interfaces/web/app.py').read_text(encoding='utf-8'))
    page = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_chat_page')
    calls = {ast.unparse(node.func): node.lineno for node in ast.walk(page) if isinstance(node, ast.Call)}
    assert calls['workspace_ui.panels'] < calls['st.chat_input']
