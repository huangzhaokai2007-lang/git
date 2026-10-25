from unittest.mock import Mock


def test_reply_reveal_runs_only_once_and_preserves_markdown(monkeypatch):
    from interfaces.web import reply_animation as ui
    fake = Mock()
    fake.session_state = {}
    fake.get_option.return_value = False
    received = []
    fake.write_stream.side_effect = lambda chunks: received.extend(chunks)
    monkeypatch.setattr(ui, 'st', fake)
    monkeypatch.setattr(ui.time, 'sleep', lambda _: None)
    text = '**您好**\n\n请确认银行卡。'
    ui.render(text, 'turn-1', animate=True)
    assert ''.join(received) == text
    assert fake.write_stream.call_count == 1
    ui.render(text, 'turn-1', animate=True)
    assert fake.write_stream.call_count == 1
    fake.markdown.assert_called_with(text)


def test_old_replies_are_immediate_and_long_reply_has_bounded_delay(monkeypatch):
    from interfaces.web import reply_animation as ui
    fake = Mock()
    fake.session_state = {}
    fake.get_option.return_value = False
    monkeypatch.setattr(ui, 'st', fake)
    delays = []
    monkeypatch.setattr(ui.time, 'sleep', delays.append)
    ui.render('历史消息', 'old', animate=False)
    fake.write_stream.assert_not_called()
    assert ''.join(ui.chunks('长' * 5000)) == '长' * 5000
    assert sum(delays) <= 2.61


def test_long_replies_do_not_flood_browser_layout(monkeypatch):
    from interfaces.web import reply_animation as ui
    delays = []
    monkeypatch.setattr(ui.time, 'sleep', delays.append)
    text = '长回复内容' * 1000
    updates = list(ui.chunks(text))
    assert ''.join(updates) == text
    assert len(updates) <= 80
    assert min(delays) >= 1 / 30 - 1e-6
    assert sum(delays) <= 2.61
