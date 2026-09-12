"""Auth-mode resolution and Entra ID role mapping.

AUTH_MODE (settings.env): 'local' (default, SQLite accounts) or 'entra'
(Microsoft Entra ID via Streamlit native OIDC). In 'entra' mode, roles are
derived from ADMIN_EMAILS — any signed-in Entra user whose email is listed is an
admin; everyone else is an analyst.
"""
import os


def auth_mode() -> str:
    return os.getenv("AUTH_MODE", "local").strip().lower()


def admin_emails() -> list[str]:
    return [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]


def role_for_email(email: str) -> str:
    return "admin" if (email or "").strip().lower() in admin_emails() else "analyst"
