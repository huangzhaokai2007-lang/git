"""守卫：解析完收款人后，TA 必须出现在群收款卡面的参与人里。

用户 2026-10-30：「进行收款人解析之后却没有在参与人中加入李四」。

根因：登记补槽时存的是**原始姓名**（`李四`），下一轮卡面把姓名映射成 id 时因为同名
两位"不敢猜"就跳过了 → 人丢失。现在补槽登记存 **shape 之后**的槽位（含 payee_id），
卡面预填优先用 id。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import classifier, llm

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import demo                                                       # noqa: E402


def test_picked_payee_appears_in_participants(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    os.environ["DB_PATH"] = str(demo.build_temp_db())

    def chat_json(system: str, user: object, schema):
        if schema is classifier.IntentOut:
            return schema.model_validate({"intent": "aa_collect", "confidence": 0.95,
                                          "slots": {"payee_ids": ["李四", "王五"]},
                                          "missing_slots": ["amount"]})
        raise llm.LLMUnavailable("测试：除意图分类外不给 LLM 输出")

    monkeypatch.setattr(llm, "chat_json", chat_json)
    app = AppTest.from_file(str(REPO / "interfaces" / "web" / "app.py"), default_timeout=300)
    app.run()
    app.chat_input[0].set_value("向李四和王五群收款").run()
    assert not app.exception, app.exception
    assert len([button for button in app.button if button.label == "选这位"]) == 2

    next(button for button in app.button if button.label == "选这位").click().run()
    assert not app.exception, app.exception

    marks = "\n".join(str(element.value) for element in app.markdown)
    chips = re.findall(r'aa-chip">([^<]+)<', marks)
    assert any("王五" in chip for chip in chips), chips
    assert any("李四" in chip for chip in chips), f"解析完的李四没进参与人：{chips}"
    assert any("139****" in chip for chip in chips), "同名的李四应带脱敏号区分"
