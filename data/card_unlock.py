"""Prove that the current lock came from the self-service temporary-lock flow."""
import sqlite3

from data import card_lock


def eligible(conn: sqlite3.Connection, card_id: str, user_id: str) -> dict | None:
    row = card_lock.eligible_card(conn, card_id, user_id)
    if not row or row['status'] != 'locked':
        return None
    account = conn.execute('SELECT status FROM account WHERE id=?', (row['account_id'],)).fetchone()
    if not account or account['status'] != 'active':
        return None
    events = conn.execute(
        "SELECT rowid,intent,trace_id,session_id FROM audit_log WHERE result='success' "
        "AND json_extract(params_json,'$.card_id')=? "
        "AND intent IN ('card_lock','card_unlock','card_report_lost') "
        "AND (json_extract(params_json,'$.action') IN ('lock','unlock','report_lost')) "
        "ORDER BY rowid DESC LIMIT 2", (card_id,)).fetchall()
    if not events or events[0]['intent'] != 'card_lock':
        return None
    latest = events[0]
    previous = events[1]['rowid'] if len(events) > 1 else 0
    preview = conn.execute(
        "SELECT 1 FROM audit_log WHERE tool='preview_card_lock' AND result='pending_confirm' "
        "AND session_id=? AND json_extract(params_json,'$.card_id')=? AND trace_id=? AND rowid>? AND rowid<?",
        (latest['session_id'], card_id, latest['trace_id'], previous, latest['rowid']),
    ).fetchone()
    return {**row, 'lock_trace': latest['trace_id']} if preview else None
