"""Jira ticket agent - create a Jira issue for a below-threshold assessment.

Safe no-op when JIRA_* is not configured (mirrors results_store / notify). Uses the
Jira Cloud REST API v2 with basic auth (email + API token) over stdlib urllib, so no
extra dependency is required.

config/settings.env:
  JIRA_URL          e.g. https://compliancegapanalyzer.atlassian.net
  JIRA_EMAIL        your Atlassian account email
  JIRA_API_TOKEN    an Atlassian API token (id.atlassian.com -> Security -> API tokens)
  JIRA_PROJECT_KEY  e.g. KAN
  JIRA_ISSUE_TYPE   default: Task
"""
import base64
import json
import os
import urllib.error
import urllib.request

from backend import tracing

_URL = os.getenv("JIRA_URL", "").strip().rstrip("/")
_EMAIL = os.getenv("JIRA_EMAIL", "").strip()
_TOKEN = os.getenv("JIRA_API_TOKEN", "").strip()
_PROJECT = os.getenv("JIRA_PROJECT_KEY", "").strip()
_ISSUETYPE = os.getenv("JIRA_ISSUE_TYPE", "Task").strip() or "Task"


def is_configured() -> bool:
    return bool(_URL and _EMAIL and _TOKEN and _PROJECT)


def _top_gaps(result, limit: int = 8):
    rows = [c for c in result.get("controls", []) if c.get("state") in ("Gap", "Partial")]
    risk_order = {"High": 0, "Medium": 1, "Low": 2}
    rows.sort(key=lambda c: (0 if c.get("state") == "Gap" else 1, risk_order.get(c.get("risk"), 3)))
    return rows[:limit]


def _title(result, entity) -> str:
    return f"[Compliance {result.get('compliance_score')}%] {entity} - below threshold"


def _description(result, entity) -> str:
    s = result.get("summary", {})
    lines = [
        f"Entity: {entity}",
        f"Compliance score: {result.get('compliance_score')}%",
        f"Covered {s.get('covered')} / Partial {s.get('partial')} / Gap {s.get('gap')} "
        f"of {s.get('total')}",
        f"Correlation id: {result.get('correlation_id', '-')}",
        "",
        "Top gaps to remediate:",
    ]
    for c in _top_gaps(result):
        lines.append(f"- [{c.get('state')}, risk {c.get('risk', '-')}] "
                     f"{c.get('control_id')} - {c.get('title')}")
        if c.get("narrative"):
            lines.append(f"    {c['narrative']}")
    lines += ["", "- Created automatically by the Compliance Gap Analyzer."]
    return "\n".join(lines)


def _auth() -> str:
    return base64.b64encode(f"{_EMAIL}:{_TOKEN}".encode()).decode()


def _valid_issuetypes() -> list[tuple[str, str]]:
    """Return [(id, name)] of non-subtask issue types for the project (or [])."""
    try:
        req = urllib.request.Request(
            f"{_URL}/rest/api/2/issue/createmeta?projectKeys={_PROJECT}&expand=projects.issuetypes",
            headers={"Authorization": f"Basic {_auth()}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        for p in data.get("projects", []):
            return [(it["id"], it["name"]) for it in p.get("issuetypes", [])
                    if not it.get("subtask")]
    except Exception:
        pass
    return []


def _resolve_issuetype() -> dict:
    """Prefer the configured type name; if the project doesn't have it, pick a valid one."""
    types = _valid_issuetypes()
    if not types:
        return {"name": _ISSUETYPE}  # can't check - fall back to the configured name
    for tid, name in types:
        if name.lower() == _ISSUETYPE.lower():
            return {"id": tid}
    # configured name not found - avoid Epic if a plainer type exists
    non_epic = [(tid, name) for tid, name in types if name.lower() != "epic"]
    tid = (non_epic or types)[0][0]
    return {"id": tid}


def preview(result, entity) -> dict:
    """Return what would be created, so the analyst can review before creating."""
    return {"project": _PROJECT, "issuetype": _ISSUETYPE,
            "summary": _title(result, entity), "description": _description(result, entity)}


def create_issue(result, entity, user_id=None) -> tuple[bool, str, str | None]:
    """Create a Jira issue. Returns (ok, message, browse_url_or_None). Never raises."""
    with tracing.task("jira-ticket",
                      input={"entity": entity, "score": result.get("compliance_score")},
                      user_id=user_id, session_id=result.get("correlation_id"),
                      tags=["jira-ticket"]) as _task:
        ok, msg, url = _create_issue(result, entity)
        _task.update(output={"ok": ok, "message": msg, "url": url},
                     level=None if ok else "ERROR", status_message=None if ok else str(msg)[:200])
        return ok, msg, url


def _create_issue(result, entity) -> tuple[bool, str, str | None]:
    if not is_configured():
        return False, "Jira is not configured (set JIRA_* in settings.env).", None

    def _post(issuetype: dict):
        payload = {"fields": {
            "project": {"key": _PROJECT},
            "summary": _title(result, entity),
            "description": _description(result, entity),
            "issuetype": issuetype,
        }}
        req = urllib.request.Request(
            f"{_URL}/rest/api/2/issue", data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Basic {_auth()}",
                     "Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        body = _post({"name": _ISSUETYPE})
        key = body.get("key")
        return True, f"Created {key}", f"{_URL}/browse/{key}"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        # Auto-recover: if the issue type is invalid, resolve a valid one and retry once.
        if e.code == 400 and "issuetype" in detail.lower():
            try:
                body = _post(_resolve_issuetype())
                key = body.get("key")
                return True, f"Created {key}", f"{_URL}/browse/{key}"
            except Exception as e2:
                return False, f"Jira retry failed: {e2}", None
        return False, f"Jira API error {e.code}: {detail[:300]}", None
    except Exception as e:
        return False, f"Jira request failed: {e}", None
