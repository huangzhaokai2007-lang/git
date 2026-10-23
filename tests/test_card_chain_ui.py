"""链条动画只读取真实状态，预览和验证码失败不能显示锁定成功。"""
from tests.test_card_binding_ui import CARD, app, bind, click


def marker(at, status):
    return any(f'data-card="{CARD}" data-status="{status}"' in item.value for item in at.markdown)


def test_chains_follow_successful_lock_and_unlock_only(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.radio[0].set_value('💳 我的银行卡').run()
    bind(at)
    assert marker(at, 'normal')
    next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
    assert marker(at, 'normal')
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('000000')
    click(at, '确认锁定')
    assert marker(at, 'normal')
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('123456')
    click(at, '确认锁定')
    assert marker(at, 'locked')
    next(button for button in at.button if button.key == f'lock-toggle-{CARD}').click().run()
    assert marker(at, 'locked')
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('000000')
    click(at, '确认解锁')
    assert marker(at, 'locked')
    next(item for item in at.text_input if item.key == f'lock-inline-otp-{CARD}').set_value('123456')
    click(at, '确认解锁')
    assert marker(at, 'normal')
