"""Runtime key/value settings stored locally in auth.db (settings table).

Used for values an admin can change at runtime without editing files, e.g. the
admin notification email and feature toggles. Static/secret config (Azure keys,
SMTP credentials, Entra IDs) stays in config/settings.env.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth.db")

DEFAULTS = {
    "admin_notify_email": "",
    "notify_on_assessment": "false",
    "notify_on_user_change": "true",
    "notify_on_login_failure": "false",
    "privacy_redact_pii": "true",
    "privacy_store_results": "false",
    "audit_retention_days": "365",
}


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
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")


def get(key: str, default: str | None = None) -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    return DEFAULTS.get(key, default if default is not None else "")


def get_bool(key: str) -> bool:
    return str(get(key)).strip().lower() in ("1", "true", "yes", "on")


def set(key: str, value) -> None:
    with _conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def all_settings() -> dict:
    out = dict(DEFAULTS)
    with _conn() as c:
        for row in c.execute("SELECT key, value FROM settings").fetchall():
            out[row["key"]] = row["value"]
    return out
