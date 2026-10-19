"""F07 数据查询：只读本人已绑定卡与持久化成功审计。"""
import json
import sqlite3

from data import card_binding


def eligible_card(conn: sqlite3.Connection, card_id: str, user_id: str) -> dict | None:
    row = card_binding.owned_card(conn, card_id, user_id)
    return row if row and card_binding.is_bound(conn, card_id, user_id) else None


def records(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT a.ts,a.trace_id,a.params_json,c.card_no_mask FROM audit_log a "
        "JOIN card c ON c.id=json_extract(a.params_json,'$.card_id') "
        "WHERE a.intent IN ('card_lock','card_unlock') AND a.result='success' AND c.user_id=? "
        "ORDER BY a.ts DESC,a.rowid DESC LIMIT 100", (user_id,)).fetchall()
    return [{"时间": row['ts'], "银行卡": row['card_no_mask'],
             "操作": "解锁" if json.loads(row['params_json']).get('action') == 'unlock' else "锁卡",
             "原状态": json.loads(row['params_json']).get('status_before'),
             "结果": "已恢复正常" if json.loads(row['params_json']).get('action') == 'unlock' else "已锁定",
             "追踪编号": row['trace_id']} for row in rows]
