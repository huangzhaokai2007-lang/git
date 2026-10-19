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
