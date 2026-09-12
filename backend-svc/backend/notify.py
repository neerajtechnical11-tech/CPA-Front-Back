"""Email notifications to the admin via SMTP.

SMTP transport/credentials come from config/settings.env; the destination admin
address is a runtime setting an admin can edit in the UI (appsettings:
admin_notify_email). If SMTP is not configured, notifications degrade gracefully
(no error thrown into the app).

settings.env keys:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_USE_TLS
"""
import os
import smtplib
from email.message import EmailMessage

from backend import appsettings


def _cfg():
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or "587"),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")).strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes", "on"),
    }


def _demo() -> bool:
    """Demo mode: report sends as successful without a live SMTP server.
    Set DEMO_EMAIL_MODE=true in settings.env while SMTP is being set up."""
    return os.getenv("DEMO_EMAIL_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def is_configured() -> bool:
    if _demo():
        return True
    c = _cfg()
    return bool(c["host"] and c["sender"])


def admin_email() -> str:
    return appsettings.get("admin_notify_email", "").strip()


def send_email(subject: str, body: str, to: str | None = None) -> tuple[bool, str]:
    """Send an email; returns (ok, message). Never raises into the caller."""
    c = _cfg()
    recipient = (to or admin_email()).strip()
    if _demo():
        return True, f"Sent to {recipient or '(no recipient)'}. (demo mode - SMTP not yet live)"
    if not is_configured():
        return False, "SMTP is not configured (set SMTP_* in settings.env)."
    if not recipient:
        return False, "No admin notification email set."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = c["sender"]
    msg["To"] = recipient
    msg.set_content(body)
    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=15) as s:
            if c["use_tls"]:
                s.starttls()
            if c["user"]:
                s.login(c["user"], c["password"])
            s.send_message(msg)
        return True, f"Sent to {recipient}."
    except Exception as e:
        return False, f"Send failed: {e}"


def notify_admin(subject: str, body: str) -> tuple[bool, str]:
    """Send to the configured admin address (no-op if unset/unconfigured)."""
    return send_email(subject, body)


def send_test() -> tuple[bool, str]:
    return send_email("Compliance Gap Analyzer - test notification",
                      "This is a test email confirming notifications are configured correctly.")


def connectivity() -> tuple[bool, str]:
    """Liveness check for the SMTP server (connect + STARTTLS, no send/login)."""
    c = _cfg()
    if not is_configured():
        return False, "not configured"
    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=10) as s:
            if c["use_tls"]:
                s.starttls()
            s.ehlo()
        return True, f"reachable at {c['host']}:{c['port']}"
    except Exception as e:
        return False, f"unreachable: {e}"
