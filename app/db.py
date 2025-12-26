import sqlite3
from typing import Optional, Dict, Any

DB = "/opt/services/paybot/data/db.sqlite3"

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    return c

def create_request(
    author_id: int,
    author_name: str,
    title: str,
    amount: float,
    payment_type: str,
    budget_category: str,
    attachment_file_id: Optional[str],
    attachment_kind: Optional[str],
):
    with conn() as c:
        cur = c.execute(
            """
            INSERT INTO requests(
              author_tg_id, author_name, title, amount, status, exported_to_sheets,
              attachment_file_id, attachment_kind, payment_type, budget_category
            )
            VALUES (?, ?, ?, ?, 'new', 0, ?, ?, ?, ?)
            """,
            (author_id, author_name, title.strip(), float(amount),
             attachment_file_id, attachment_kind, payment_type, budget_category),
        )
        c.commit()
        return cur.lastrowid

def get_request(req_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()

def add_comment(req_id: int, author_id: int, author_name: str, text: str):
    with conn() as c:
        c.execute(
            "INSERT INTO comments(request_id, author_tg_id, author_name, text) VALUES (?,?,?,?)",
            (req_id, author_id, author_name, (text or "").strip()),
        )
        c.commit()

def set_decision(req_id: int, status: str, admin_id: int, admin_name: str, decision_comment: str) -> int:
    with conn() as c:
        cur = c.execute(
            """
            UPDATE requests
            SET status = ?,
                decision_at = datetime('now'),
                decision_by_tg_id = ?,
                decision_by_name = ?,
                decision_comment = ?,
                exported_to_sheets = 0
            WHERE id = ? AND status IN ('new','rework')
            """,
            (status, admin_id, admin_name, (decision_comment or "").strip(), req_id),
        )
        c.commit()
        return cur.rowcount

def set_status(req_id: int, status: str) -> int:
    with conn() as c:
        cur = c.execute("UPDATE requests SET status=? WHERE id=?", (status, req_id))
        c.commit()
        return cur.rowcount

def update_request_fields(req_id: int, fields: Dict[str, Any]) -> int:
    allowed = {"title","amount","payment_type","budget_category"}
    keys = [k for k in fields.keys() if k in allowed]
    if not keys:
        return 0
    sets = ", ".join([f"{k}=?" for k in keys])
    vals = [fields[k] for k in keys] + [req_id]
    with conn() as c:
        cur = c.execute(f"UPDATE requests SET {sets} WHERE id=? AND status IN ('new','rework')", vals)
        c.commit()
        return cur.rowcount

def get_comments(req_id: int, limit: int = 10):
    with conn() as c:
        return c.execute(
            "SELECT * FROM comments WHERE request_id=? ORDER BY id DESC LIMIT ?",
            (req_id, limit),
        ).fetchall()
