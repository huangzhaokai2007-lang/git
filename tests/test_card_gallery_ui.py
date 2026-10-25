"""卡片式入口仍保护完整卡号，并保留余额和锁卡独立操作。"""
import pytest

from tests.test_card_binding_ui import CARD, NUMBER, app, bind, click
from tests.conftest import raw


def test_bank_card_face_opens_details_without_revealing_number(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    face = next(button for button in at.button if button.key == f'detail-{CARD}')
    assert face.proto.help == '点击卡片查看详情'
    face.click().run()
    assert not at.exception
    assert any(button.label == '查看完整卡号' for button in at.button)
    assert not any(NUMBER in element.value for element in at.markdown)
    assert not any('46,634.00' in element.value for element in at.text)
    next(button for button in at.button if button.key == f'balance-{CARD}').click().run()
    assert any('46,634.00' in element.value for element in at.text)
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'
    assert any(button.key == f'lock-toggle-{CARD}' for button in at.button)


def test_close_focus_restores_gallery_and_clears_sensitive_detail(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    assert any(f'data-selected="{CARD}"' in item.value for item in at.markdown)
    click(at, '关闭详情')
    assert not at.exception
    assert any('data-selected=""' in item.value for item in at.markdown)
    assert not any(button.label == '查看完整卡号' for button in at.button)
    assert any(button.key == f'detail-{CARD}' for button in at.button)
    assert '****** 元' in [item.value for item in at.text]


def test_reopening_same_card_does_not_repeat_detail_chat_message(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    before = list(at.session_state['messages'])
    for _ in range(3):
        click(at, '6222 **** **** 0001')
    click(at, '关闭详情')
    click(at, '6222 **** **** 0001')

    messages = list(at.session_state['messages'])
    assert len(messages) == len(before) + 1
    assert messages[:len(before)] == before
    assert sum(bool(item.get('binding_detail')) for item in messages) == 1
    assert any(button.label == '查看完整卡号' for button in at.button)
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == 'normal'

    # Older sessions can already contain copies; keep the first and remove the rest.
    at.session_state['messages'] = messages + [dict(messages[-1]), dict(messages[-1])]
    click(at, '6222 **** **** 0001')
    assert list(at.session_state['messages']) == messages


def test_filter_change_leaves_focus_instead_of_showing_an_unlisted_card(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    click(at, '6222 **** **** 0001')
    next(item for item in at.selectbox if item.label == '卡片状态').set_value('已锁定').run()
    assert not any(button.label == '查看完整卡号' for button in at.button)


def test_failed_detail_acknowledges_request_and_keeps_gallery_available(monkeypatch, seeded):
    from agent import card_binding_flow

    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    monkeypatch.setattr(card_binding_flow, 'detail', lambda card_id: {'ok': False, 'message': '详情暂时不可用'})
    click(at, '6222 **** **** 0001')
    assert any('详情暂时不可用' in item.value for item in at.error)
    assert any('data-selected="" data-revision="1"' in item.value for item in at.markdown)
    assert not any(button.label == '查看完整卡号' for button in at.button)
    assert any(button.key == f'detail-{CARD}' for button in at.button)


@pytest.mark.parametrize('mode', ['lock', 'unlock', 'panel'])
def test_card_details_close_and_cancel_pending_lock_preview(monkeypatch, seeded, mode):
    from agent import card_lock_flow as flow

    at = app(monkeypatch, seeded)
    click(at, '管卡')
    bind(at)
    if mode == 'unlock':
        next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
        next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('123456')
        click(at, '确认锁定')
    if mode == 'panel':
        click(at, '临时锁卡')
        click(at, '锁定 6222 **** **** 0001')
    else:
        next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
    token = at.session_state['lock-view']['preview']['token']
    session = at.session_state['session_id']
    assert any(item.label == '模拟短信验证码' for item in at.text_input)

    click(at, '6222 **** **** 0001')

    assert any(button.label == '查看完整卡号' for button in at.button)
    assert not at.session_state['lock-view']['open']
    assert at.session_state['lock-view']['preview'] is None
    assert not any(item.label == '模拟短信验证码' for item in at.text_input)
    assert not any(button.label in ('确认锁定', '确认锁卡', '确认解锁', '取消锁卡', '取消解锁') for button in at.button)
    assert not any(item.label == '临时锁卡办理须知与影响' for item in at.expander)
    submit = flow.unlock_submit if mode == 'unlock' else flow.submit
    assert not submit(token, '123456', session)['ok']
    expected = 'locked' if mode == 'unlock' else 'normal'
    assert raw(seeded, 'SELECT status FROM card WHERE id=?', (CARD,))[0]['status'] == expected
