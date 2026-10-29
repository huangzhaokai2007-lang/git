"""理财卡「日 K」按钮的只读入口：**界面 → agent → tools** 单向，界面不直接碰 tools。

与 `bill_detail_flow.load` 同构：一次只读查询、一次审计、返回 facts（不含任何写动作）。
为什么单独一层：`interfaces/**` 不许 import `tools`（见 `tests/test_web_layering.py` ②）。
"""

from __future__ import annotations

import uuid

from data import dao
from tools import wealth


def load(session_id: str, product_id: str, granularity: str = 'daily') -> dict:
    """取单只产品的 K 线（日/周/月）。失败也照常返回 `ok=False`，由界面决定怎么显示。"""
    result = wealth.product_daily(product_id, granularity=granularity)
    trace_id = f'trace-{uuid.uuid4().hex[:12]}'
    dao.insert_audit(trace_id, session_id, actor='user', intent='wealth_recommend',
                     tool='product_daily',
                     params_json={'product_id': product_id, 'granularity': granularity},
                     risk_level='L0', permission_tier='L0',
                     result='success' if result.ok else 'rejected',
                     error_code=result.error_code)
    return {'ok': result.ok, 'message': result.message, 'trace_id': trace_id,
            **(result.facts or {})}
