"""Audit logging to a local SQLite table (auth.db / audit_log).

Records who did what and when: sign-in, sign-out, user management, password
changes, license application, document assessments, report downloads, etc.
Admins can view and export the log; entries are retained for a configurable period.
"""
import csv
import datetime as dt
import io
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth.db")


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                username  TEXT,
                action    TEXT NOT NULL,
                detail    TEXT
            )"""
        )


def log(username: str | None, action: str, detail: str = "") -> None:
    """Record an audit event. Never raises into the caller."""
    try:
        with _conn() as c:
            c.execute("INSERT INTO audit_log(ts, username, action, detail) VALUES(?,?,?,?)",
                      (dt.datetime.now().isoformat(timespec="seconds"), username or "-", action, detail))
    except Exception:
        pass


def new_correlation_id() -> str:
    """A short id generated per assessment and threaded through the agents so all
    activity for one document can be retrieved together."""
    return uuid.uuid4().hex[:12]


def record(username: str | None, action: str, correlation_id: str | None = None, **fields) -> None:
    """Structured audit event: stores a JSON payload (correlation id + agent decision
    details + provenance) in the detail column. Never raises into the caller."""
    payload = {}
    if correlation_id:
        payload["cid"] = correlation_id
    payload.update(fields)
    try:
        log(username, action, json.dumps(payload, default=str))
    except Exception:
        pass


def recent(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT ts, username, action, detail FROM audit_log "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def export_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "username", "action", "detail"])
    with _conn() as c:
        for r in c.execute("SELECT ts, username, action, detail FROM audit_log ORDER BY id").fetchall():
            w.writerow([r["ts"], r["username"], r["action"], r["detail"]])
    return buf.getvalue().encode("utf-8")


def purge(older_than_days: int) -> int:
    """Delete entries older than N days; returns rows removed."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=older_than_days)).isoformat()
    with _conn() as c:
        cur = c.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
        return cur.rowcount


def count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
