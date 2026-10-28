"""Excel must recognize Chinese CSV downloads without guessing a legacy encoding."""
import codecs
import csv
import io
from contextlib import nullcontext
from types import SimpleNamespace

from interfaces.web import insight_card_ui


def test_csv_download_has_utf8_signature_and_preserves_values(monkeypatch):
    downloads = []
    monkeypatch.setattr(insight_card_ui, 'st', SimpleNamespace(
        expander=lambda *a, **kw: nullcontext(),
        download_button=lambda label, **kw: downloads.append((label, kw)),
        code=lambda *a, **kw: None))
    facts = {'items': [{'ts': '2026-09-12T00:00:00', 'counterparty': '稳盈货币基金',
        'amount_yuan': '-100.00', 'severity': 'high', 'reason': '凌晨时段交易、陌生商户交易'}]}
    insight_card_ui._export({'trace_id': 'csv-proof'}, 'anomaly_check', facts, 'csv-test')
    label, payload = downloads[0]
    assert label == '下载异常清单（CSV）'
    assert isinstance(payload['data'], bytes)
    assert payload['data'].startswith(codecs.BOM_UTF8)
    rows = list(csv.DictReader(io.StringIO(payload['data'].decode('utf-8-sig'))))
    assert rows == [{'时间': '2026-09-12 00:00', '交易对手': '稳盈货币基金',
        '金额（元）': '-100.00', '等级': 'high', '依据': '凌晨时段交易、陌生商户交易'}]
