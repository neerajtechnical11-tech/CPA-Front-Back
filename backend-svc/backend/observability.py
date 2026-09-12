"""Observability: dependency health checks and usage metrics.

Health surfaces the live status of the app's dependencies (license, Azure AI
Search, Azure OpenAI config). Metrics are derived from the audit log so admins
can see activity without any external monitoring stack.
"""
import os

from backend import audit, licensing


def _ok(name, detail):
    return {"component": name, "status": "ok", "detail": detail}


def _warn(name, detail):
    return {"component": name, "status": "warn", "detail": detail}


def _err(name, e):
    return {"component": name, "status": "error", "detail": str(e)[:180]}


def health_check() -> list[dict]:
    """Live status of every dependency. status in ok|warn|error.

    Performs real liveness pings (a tiny embedding + chat call, a Search count,
    an SMTP connect) so green/red reflects reality, not just configuration.
    """
    checks = []

    # License
    lic = licensing.check()
    checks.append({"component": "License",
                   "status": "ok" if lic["state"] in ("active", "expiring") else "error",
                   "detail": lic.get("message", "")})

    # Azure AI Search + baseline data
    try:
        from backend import ai_search_client
        n = ai_search_client.document_count()
        if n > 0:
            checks.append(_ok("Azure AI Search", f"reachable - {n} baseline controls indexed"))
        else:
            checks.append(_warn("Azure AI Search", "reachable but no baseline loaded (run ingestion)"))
    except Exception as e:
        checks.append(_err("Azure AI Search", e))

    # Azure OpenAI - embeddings (tiny live ping)
    try:
        from backend import azure_openai
        v = azure_openai.embed("ping")
        checks.append(_ok("Azure OpenAI - Embeddings",
                          f"deployment '{os.getenv('AZURE_OPENAI_EMBED_DEPLOYMENT')}' responding ({len(v)}-dim)"))
    except Exception as e:
        checks.append(_err("Azure OpenAI - Embeddings", e))

    # Azure OpenAI - chat (tiny live ping)
    try:
        from backend import azure_openai
        azure_openai.chat("Reply with: OK", max_tokens=5)
        checks.append(_ok("Azure OpenAI - Chat",
                          f"deployment '{os.getenv('AZURE_OPENAI_CHAT_DEPLOYMENT')}' responding"))
    except Exception as e:
        checks.append(_err("Azure OpenAI - Chat", e))

    # Email / SMTP
    from backend import notify
    if not notify.is_configured():
        checks.append(_warn("Email (SMTP)", "not configured - notifications disabled"))
    else:
        ok, detail = notify.connectivity()
        checks.append(_ok("Email (SMTP)", detail) if ok else _err("Email (SMTP)", detail))

    # Authentication store
    try:
        from backend import auth
        users = auth.list_users()
        checks.append(_ok("Authentication store", f"local SQLite - {len(users)} user(s)"))
    except Exception as e:
        checks.append(_err("Authentication store", e))

    return checks


def metrics() -> dict:
    """Usage metrics derived from the audit log."""
    rows = audit.recent(limit=10000)
    counts = {}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    last = rows[0]["ts"] if rows else "-"
    return {
        "total_events": len(rows),
        "assessments": counts.get("assess_document", 0),
        "sign_ins": counts.get("login", 0),
        "failed_sign_ins": counts.get("login_failed", 0),
        "reports_downloaded": counts.get("download_report", 0),
        "user_changes": counts.get("create_user", 0) + counts.get("delete_user", 0),
        "last_activity": last,
        "by_action": counts,
    }
