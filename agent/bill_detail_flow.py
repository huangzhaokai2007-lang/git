"""Read-only chart clicks with source validation and their own audit entry."""
import uuid

from data import dao
from tools import _bill_details


def load(session_id: str, source: dict, category: str) -> dict:
    """No classifier, scene mutation, draft resumption, or scheduled settlement."""
    facts = source.get('facts') or {}
    if (source.get('intent') not in ('bill_report', 'bill_analysis') or source.get('error_code')
            or not isinstance(category, str)
            or category not in [group.get('key') for group in facts.get('groups', [])]):
        return {'ok': False, 'message': '请选择该图中的支出分类。'}
    result = _bill_details.read(facts.get('period'), category, facts.get('group_by') or 'category')
    trace_id = f'trace-{uuid.uuid4().hex[:12]}'
    dao.insert_audit(trace_id, session_id, actor='user', intent='bill_analysis', tool='bill_detail_read',
                     params_json={'period': facts.get('period'), 'category': category,
                                  'source_trace_id': source.get('trace_id')},
                     risk_level='L0', permission_tier='L0', result='success' if result.ok else 'rejected',
                     error_code=result.error_code)
    return {'ok': result.ok, 'message': result.message, 'trace_id': trace_id, **result.facts}
