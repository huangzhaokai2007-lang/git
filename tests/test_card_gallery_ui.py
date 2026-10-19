"""卡片式入口仍保护完整卡号，并保留余额和锁卡独立操作。"""
from tests.test_card_binding_ui import CARD, NUMBER, app, bind, click
from tests.conftest import raw


def test_bank_card_face_opens_details_without_revealing_number(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
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
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    click(at, '6222 **** **** 0001')
    assert any(f'data-selected="{CARD}"' in item.value for item in at.markdown)
    click(at, '关闭详情')
    assert not at.exception
    assert any('data-selected=""' in item.value for item in at.markdown)
    assert not any(button.label == '查看完整卡号' for button in at.button)
    assert any(button.key == f'detail-{CARD}' for button in at.button)
    assert '******' in [item.value for item in at.text]


def test_filter_change_leaves_focus_instead_of_showing_an_unlisted_card(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    click(at, '6222 **** **** 0001')
    next(item for item in at.selectbox if item.label == '卡片状态').set_value('已锁定').run()
    assert not any(button.label == '查看完整卡号' for button in at.button)


def test_failed_detail_acknowledges_request_and_keeps_gallery_available(monkeypatch, seeded):
    from agent import card_binding_flow

    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    monkeypatch.setattr(card_binding_flow, 'detail', lambda card_id: {'ok': False, 'message': '详情暂时不可用'})
    click(at, '6222 **** **** 0001')
    assert any('详情暂时不可用' in item.value for item in at.error)
    assert any('data-selected="" data-revision="1"' in item.value for item in at.markdown)
    assert not any(button.label == '查看完整卡号' for button in at.button)
    assert any(button.key == f'detail-{CARD}' for button in at.button)
