"""F09 additive durable loss requests; no changes to core banking schema."""
import json
import sqlite3

from data import card_binding as binding

DDL = (
    "CREATE TABLE IF NOT EXISTS agent_card_lost_request ("
    "token TEXT PRIMARY KEY,user_id TEXT NOT NULL,session_id TEXT NOT NULL,card_id TEXT NOT NULL,"
    "status TEXT NOT NULL,expires_at REAL NOT NULL,execute_at REAL,verified_at REAL,"
    "application_id TEXT UNIQUE,snapshot TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS agent_lost_active_card ON agent_card_lost_request(card_id) "
    "WHERE status IN ('pending','success')",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    binding.ensure_schema(conn)
    for statement in DDL:
        conn.execute(statement)


def eligible(conn: sqlite3.Connection, card_id: str, user_id: str) -> dict | None:
    row = binding.owned_card(conn, card_id, user_id)
    return row if row and binding.is_bound(conn, card_id, user_id) else None


def get(conn: sqlite3.Connection, field: str, value: str) -> dict | None:
    if field not in ('token', 'application_id'):
        raise ValueError('invalid lookup')
    row = conn.execute(f'SELECT * FROM agent_card_lost_request WHERE {field}=?', (value,)).fetchone()
    return dict(row) if row else None


def active(conn: sqlite3.Connection, card_id: str) -> bool:
    return conn.execute("SELECT 1 FROM agent_card_lost_request WHERE card_id=? "
                        "AND status IN ('pending','success')", (card_id,)).fetchone() is not None


def create(conn: sqlite3.Connection, token: str, user: str, session: str,
           card_id: str, expires: float, snapshot: dict) -> None:
    conn.execute('INSERT INTO agent_card_lost_request '
                 '(token,user_id,session_id,card_id,status,expires_at,snapshot) '
                 "VALUES (?,?,?,?,'preview',?,?)",
                 (token, user, session, card_id, expires, json.dumps(snapshot, ensure_ascii=False)))


def update(conn: sqlite3.Connection, token: str, status: str, snapshot: dict,
           application_id: str | None = None, execute_at: float | None = None,
           verified_at: float | None = None) -> None:
    conn.execute('UPDATE agent_card_lost_request SET status=?,snapshot=?, '
                 'application_id=COALESCE(?,application_id),execute_at=COALESCE(?,execute_at),'
                 'verified_at=COALESCE(?,verified_at) WHERE token=?',
                 (status, json.dumps(snapshot, ensure_ascii=False), application_id, execute_at, verified_at, token))


def due(conn: sqlite3.Connection, now: float) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM agent_card_lost_request "
                                             "WHERE status='pending' AND execute_at<=?", (now,))]


def records(conn: sqlite3.Connection, user: str) -> list[dict]:
    return [json.loads(row['snapshot']) for row in conn.execute(
        "SELECT snapshot FROM agent_card_lost_request WHERE user_id=? AND status!='preview' "
        "ORDER BY verified_at DESC,rowid DESC LIMIT 100", (user,))]


def profile(conn: sqlite3.Connection, user: str) -> dict:
    row = conn.execute('SELECT name,phone FROM user WHERE id=?', (user,)).fetchone()
    if not row:
        return {'customer_name_mask': '未提供', 'phone_mask': '未提供'}
    name = row['name'] or ''
    phone = row['phone'] or ''
    return {'customer_name_mask': name[:1] + '**' if name else '未提供',
            'phone_mask': phone[:3] + '****' + phone[-4:] if len(phone) >= 7 else '未提供'}
