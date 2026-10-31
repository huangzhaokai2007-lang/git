"""深色工作区的快捷入口契约与无写入导航回归。"""
from tests.conftest import raw
from tests.test_card_binding_ui import app, click, fill


SHORTCUTS = (
    '看账单', '收款人', '管订阅', '管卡', '调整额度',
    '我的理财', '推荐理财', '锁卡', '挂失补卡', '操作记录',
)


def test_sidebar_shortcuts_use_plain_labels_and_material_icons(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    shortcuts = [button for button in at.sidebar.button if (button.key or '').startswith('scene-')]
    assert [button.label for button in shortcuts] == list(SHORTCUTS)
    assert [button.key for button in shortcuts] == [f'scene-{i}' for i in range(10)]
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
    assert at.session_state['active-scene'] == 3
    click(at, '绑定银行卡')
    fill(at, '模拟银行卡号', '6222000000000001')
    turns_before = list(at.session_state['turns'])
    messages_before = list(at.session_state['messages'])
    click(at, '操作记录')
    assert at.session_state['active-scene'] == 9
    assert at.session_state['turns'] == turns_before
    assert at.session_state['messages'] == messages_before
    #: 绑卡表单**应当关闭**（2026-10-26 修）。这里原先断言"表单还在、值还在"，是 10-24
    #: 的老契约；6bee047（10-25，"replace the active workspace on shortcut navigation"）
    #: 把快捷入口改成替换工作区后，作者同步更新了 `test_web_single_chat.py::
    #: test_operation_history_keeps_chat_and_closes_binding_form`，却漏了本文件 —— 两条用例
    #: 对同一动作要求互斥的结果，不可能同时绿。以**较新**的那份契约为准。
    #: 注意：本用例真正要守的是下面那两条"导航不查库、不落绑定记录"，草稿保留由
    #: `card_limit` 那侧负责（见 `interfaces/web/shortcut_ui.start` 的取舍说明）。
    assert not any(item.label == '模拟银行卡号' for item in at.text_input)
    assert raw(seeded, 'SELECT * FROM card') == cards_before
    assert raw(seeded, 'SELECT * FROM agent_card_binding') == []
