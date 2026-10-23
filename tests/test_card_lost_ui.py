"""F09 用户路径：意图分流、只读步骤、详细说明和显式提交。"""
from tests.test_card_binding_ui import app, bind, click, fill
from tests.conftest import raw
from datetime import timedelta


def test_ambiguous_loss_offers_both_paths(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    at.chat_input[0].set_value('帮我挂失').run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert '临时锁卡，继续寻找' in labels
    assert '确认挂失并补卡' in labels


def test_replacement_steps_disclose_scope_and_do_not_execute(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    at.chat_input[0].set_value('确定被偷了，要补一张').run()
    assert not at.exception
    click(at, '办理 6222 **** **** 0001')
    assert any('不冻结关联账户资金' in e.value for e in at.warning)
    assert next(b for b in at.button if b.label == '下一步：补卡信息').disabled
    next(c for c in at.checkbox if c.label == '我已阅读并理解办理须知').check().run()
    click(at, '下一步：补卡信息')
    assert any(s.label == '领卡网点' for s in at.selectbox)
    assert any('模拟' in e.value for e in at.caption)
    click(at, '下一步：验证确认')
    assert any(e.label == '模拟银行卡密码' for e in at.text_input)
    fill(at, '模拟银行卡密码', '000000')
    fill(at, '模拟短信验证码', '123456')
    next(c for c in at.checkbox if c.label == '确认原卡失效及补卡信息，提交本次申请').check().run()
    click(at, '确认挂失并提交补卡申请')
    assert not at.exception
    assert any('密码' in e.value or '验证' in e.value for e in at.error)
    assert raw(seeded, "SELECT status FROM card WHERE id='card_savings_0001'")[0]['status'] == 'normal'


def test_temporary_lock_has_detailed_notice(monkeypatch, seeded):
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    click(at, '临时锁卡')
    click(at, '锁定 6222 **** **** 0001')
    text = '\n'.join(e.value for e in at.markdown)
    assert '临时锁卡办理须知' in text
    assert '关联账户' in text and '挂失' in text and '验证码' in text


def test_verified_application_reaches_pending_then_success(monkeypatch, seeded):
    from tools import card_lost
    at = app(monkeypatch, seeded)
    click(at, '💳 管卡')
    bind(at)
    click(at, '挂失并补卡')
    click(at, '办理 6222 **** **** 0001')
    next(c for c in at.checkbox if c.label == '我已阅读并理解办理须知').check().run()
    click(at, '下一步：补卡信息')
    next(s for s in at.selectbox if s.label == '领卡网点').set_value('demo_east').run()
    click(at, '下一步：验证确认')
    next(c for c in at.checkbox if c.label == '确认原卡失效及补卡信息，提交本次申请').check()
    fill(at, '模拟银行卡密码', '314159')
    fill(at, '模拟短信验证码', '123456')
    click(at, '确认挂失并提交补卡申请')
    assert any('挂失待生效' in e.value for e in at.info)
    before = raw(seeded, 'SELECT * FROM account')
    now = card_lost._now()
    monkeypatch.setattr(card_lost, '_now', lambda: now + timedelta(seconds=61))
    at.run()
    assert not at.exception
    assert any('原卡已挂失' in e.value for e in at.success)
    assert raw(seeded, "SELECT status FROM card WHERE id='card_savings_0001'")[0]['status'] == 'lost'
    assert raw(seeded, 'SELECT * FROM account') == before
