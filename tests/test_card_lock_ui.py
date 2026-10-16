"""F07 实际表单交互：显式选卡与确认、取消不写。"""
from tests.test_card_binding_ui import CARD, app, bind, click, fill
from tests.conftest import raw


def test_sidebar_lock_and_record_page(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    click(at, '🔒 锁卡')
    click(at, '锁定 6222 **** **** 0001')
    assert raw(seeded, "SELECT status FROM card WHERE id='card_savings_0001'")[0]['status'] == 'normal'
    fill(at, '模拟短信验证码', '123456')
    click(at, '确认锁卡')
    assert any('锁卡成功' in item.value for item in at.success)
    click(at, '查看锁卡记录')
    assert at.radio[0].value == '💳 我的银行卡'
    assert any(tab.label == '锁卡记录' for tab in at.tabs)
    assert len(at.dataframe[-1].value) == 1


def test_chat_cancel_does_not_lock(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    at.radio[0].set_value('💬 聊天').run()
    at.chat_input[0].set_value('暂时锁定银行卡').run()
    click(at, '锁定 6222 **** **** 0001')
    click(at, '取消锁卡')
    assert not at.exception
    assert raw(seeded, "SELECT status FROM card WHERE id='card_savings_0001'")[0]['status'] == 'normal'


def test_lock_from_chat_card_list_can_confirm(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    click(at, '临时锁卡')
    click(at, '锁定 6222 **** **** 0001')
    assert not at.exception
    fill(at, '模拟短信验证码', '123456')
    click(at, '确认锁卡')
    assert any('锁卡成功' in item.value for item in at.success)


def test_card_icon_opens_inline_confirmation_and_keeps_other_controls(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    button = next(button for button in at.button if button.key == f'lock-toggle-{CARD}')
    assert button.proto.icon == ':material/lock_open:'
    button.click().run()
    assert not at.exception
    assert not at.get('dialog')
    assert not any('临时锁定银行卡' in item.value for item in at.markdown)
    assert any(item.key == f'lock-inline-otp-{CARD}' for item in at.text_input)
    next(button for button in at.button if button.key == f'balance-{CARD}').click().run()
    assert any('46,634.00' in item.value for item in at.text)
    click(at, '6222 **** **** 0001')
    assert any(button.label == '查看完整卡号' for button in at.button)
    assert raw(seeded, "SELECT status FROM card WHERE id=?", (CARD,))[0]['status'] == 'normal'
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('000000')
    click(at, '确认锁定')
    assert raw(seeded, "SELECT status FROM card WHERE id=?", (CARD,))[0]['status'] == 'normal'
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('123456')
    click(at, '确认锁定')
    button = next(button for button in at.button if button.key == f'lock-toggle-{CARD}')
    assert button.proto.icon == ':material/lock:' and not button.disabled
    assert raw(seeded, "SELECT status FROM card WHERE id=?", (CARD,))[0]['status'] == 'locked'
    button.click().run()
    assert not any(item.label == '银行卡密码' for item in at.text_input)
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('123456')
    click(at, '确认解锁')
    assert raw(seeded, "SELECT status FROM card WHERE id=?", (CARD,))[0]['status'] == 'normal'


def test_chat_lock_selection_is_inline(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '🔒 锁卡')
    assert not at.exception
    assert not at.get('dialog')
    assert any('临时锁定银行卡' in item.value for item in at.markdown)


def test_missing_card_offers_temporary_lock_without_executing(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    at.chat_input[0].set_value('我找不到我的信用卡了').run()
    assert not at.exception
    assert any(button.label == '临时锁定' for button in at.button)
    assert any(button.label == '挂失说明' for button in at.button)
    click(at, '临时锁定')
    assert any(button.label == '锁定 6222 **** **** 0001' for button in at.button)
    assert raw(seeded, "SELECT status FROM card WHERE id=?", (CARD,))[0]['status'] == 'normal'
