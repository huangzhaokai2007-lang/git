"""深色工作区的快捷入口契约与无写入导航回归。"""
from tests.conftest import raw
from tests.test_card_binding_ui import app, click, fill


SHORTCUTS = (
    '看账单', '转账', '收款人', '管订阅', '管卡', '调整额度',
    '我的理财', '全部产品', '锁卡', '挂失补卡', '操作记录',
)


def test_sidebar_shortcuts_use_plain_labels_and_material_icons(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    shortcuts = [button for button in at.sidebar.button if (button.key or '').startswith('scene-')]
    assert [button.label for button in shortcuts] == list(SHORTCUTS)
    assert [button.key for button in shortcuts] == [f'scene-{i}' for i in range(11)]
    assert all(button.proto.icon.startswith(':material/') and button.proto.icon.endswith(':')
               for button in shortcuts)
    assert not at.sidebar.get('column')
    assert not at.radio


def test_active_shortcut_preserves_draft_and_history_never_queries(monkeypatch, seeded):
    from tools import card_binding, card_limit

    at = app(monkeypatch, seeded)
    # 首次查询会惰性建立合成演示卡；先完成数据准备，再观察导航是否改卡。
    assert card_binding.list_bound_cards(audit=False).ok
    assert card_limit.records().ok
    cards_before = raw(seeded, 'SELECT * FROM card')
    click(at, '管卡')
    turn = at.session_state['turns'][-1]['turn']
    assert turn['intent'] == 'card_query'
    assert turn['tool_calls'] == ['list_bound_cards']
    assert not turn['executed']
    assert raw(seeded, 'SELECT * FROM card') == cards_before
    assert raw(seeded, 'SELECT * FROM agent_card_binding') == []
    assert at.session_state['active-scene'] == 4
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    turns_before = list(at.session_state['turns'])
    messages_before = list(at.session_state['messages'])
    click(at, '操作记录')
    assert at.session_state['active-scene'] == 10
    assert at.session_state['turns'] == turns_before
    assert at.session_state['messages'] == messages_before
    assert next(item for item in at.text_input if item.label == '模拟银行卡号').value == '6222000000000001'
    assert raw(seeded, 'SELECT * FROM card') == cards_before
    assert raw(seeded, 'SELECT * FROM agent_card_binding') == []
