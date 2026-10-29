"""Optional wording changes must never break a verified tool receipt."""
from types import SimpleNamespace

import pytest

from agent import templates


@pytest.mark.parametrize('response', [None, {}, SimpleNamespace(),
    SimpleNamespace(reply=None), SimpleNamespace(reply=123), SimpleNamespace(reply=[])])
def test_malformed_polish_response_falls_back_without_raising(monkeypatch, response):
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args: response)
    assert templates.polish('Verified receipt', {}) is None


def test_well_formed_polish_response_keeps_existing_behavior(monkeypatch):
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args: templates._Polished(reply='  Receipt  '))
    assert templates.polish('Verified receipt', {}) == 'Receipt'


def test_none_polish_response_preserves_verified_template_receipt(monkeypatch):
    from tools.schemas import ToolResult
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args: None)
    monkeypatch.setattr(templates, 'render', lambda *args: 'Verified receipt')
    assert templates.compose_reply('balance_query', {}, ToolResult(ok=True, data={}, facts={})) == (
        'Verified receipt', False, 1)


def test_template_only_scope_skips_model_and_restores_normal_polishing(monkeypatch):
    from tools.schemas import ToolResult
    calls = []
    monkeypatch.setattr(templates, 'render', lambda *args: 'Verified receipt')
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args:
                        calls.append(args) or templates._Polished(reply='Polished receipt'))
    result = ToolResult(ok=True, data={}, facts={})
    with templates.template_only():
        assert templates.compose_reply('balance_query', {}, result) == ('Verified receipt', False, 0)
    assert not calls
    assert templates.compose_reply('balance_query', {}, result) == ('Polished receipt', False, 1)
    assert len(calls) == 1


def test_template_only_scope_resets_even_after_exception(monkeypatch):
    from tools.schemas import ToolResult
    monkeypatch.setattr(templates, 'render', lambda *args: 'Verified receipt')
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args: templates._Polished(reply='Polished receipt'))
    with pytest.raises(RuntimeError):
        with templates.template_only():
            raise RuntimeError('read failed')
    assert templates.compose_reply('balance_query', {}, ToolResult(ok=True, data={}, facts={}))[0] == 'Polished receipt'


def test_sidebar_balance_refresh_uses_scoped_template_only(monkeypatch):
    from agent import balance_flow
    from tools.schemas import ToolResult
    monkeypatch.setattr(templates, 'render', lambda *args: 'Verified receipt')
    monkeypatch.setattr(templates.llm, 'chat_json', lambda *args: pytest.fail('sidebar should not call LLM'))
    monkeypatch.setattr(balance_flow.orchestrator, 'handle', lambda *args, **kwargs:
        templates.compose_reply('balance_query', {}, ToolResult(ok=True, data={}, facts={})))
    assert balance_flow.refresh('session') == ('Verified receipt', False, 0)
