"""Local authentication & authorization backed by SQLite (no DB server needed).

- Single file `auth.db` in the project root.
- Passwords stored as PBKDF2-SHA256 with a per-user salt (never plaintext).
- Roles: 'admin' (can manage users) and 'analyst' (can run assessments).
- Seeds a default admin (admin/admin) on first run — CHANGE THIS after first login.
"""
import datetime
import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth.db")
_ITERATIONS = 200_000


@contextmanager
def _conn():
    """Open a connection, commit on success, and always close (avoids file locks)."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def _insert(c: sqlite3.Connection, username: str, password: str, role: str):
    salt = secrets.token_hex(16)
    c.execute(
        "INSERT INTO users(username, salt, password_hash, role, created_at) VALUES(?,?,?,?,?)",
        (username, salt, _hash(password, salt), role, datetime.datetime.utcnow().isoformat(timespec="seconds")),
    )


def init_db():
    """Create the users table and seed the default admin if empty."""
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                salt          TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'analyst',
                created_at    TEXT NOT NULL
            )"""
        )
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            _insert(c, "admin", "admin", "admin")


def verify(username: str, password: str) -> str | None:
    """Return the user's role if credentials are valid, else None."""
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if not row:
        return None
    if secrets.compare_digest(_hash(password, row["salt"]), row["password_hash"]):
        return row["role"]
    return None


def create_user(username: str, password: str, role: str = "analyst"):
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("Username and password are both required.")
    if role not in ("admin", "analyst"):
        raise ValueError("Role must be 'admin' or 'analyst'.")
    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError(f"User '{username}' already exists.")
        _insert(c, username, password, role)


def set_password(username: str, password: str):
    if not password:
        raise ValueError("Password required.")
    with _conn() as c:
        row = c.execute("SELECT salt FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            raise ValueError("No such user.")
        c.execute("UPDATE users SET password_hash=? WHERE username=?", (_hash(password, row["salt"]), username))


def count_admins() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]


def change_password(username: str, old_password: str, new_password: str):
    """Verify the current password, then set a new one."""
    if verify(username, old_password) is None:
        raise ValueError("Current password is incorrect.")
    if not new_password:
        raise ValueError("New password cannot be empty.")
    set_password(username, new_password)


def delete_user(username: str):
    """Delete a user, refusing to remove the last remaining admin."""
    with _conn() as c:
        row = c.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            raise ValueError("No such user.")
        if row["role"] == "admin":
            admins = c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if admins <= 1:
                raise ValueError("Cannot delete the last remaining admin.")
        c.execute("DELETE FROM users WHERE username=?", (username,))


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT username, role, created_at FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]
