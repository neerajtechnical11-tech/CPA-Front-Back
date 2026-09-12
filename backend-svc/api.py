"""FastAPI backend for the 2-container split.

Wraps the EXISTING backend logic (pipeline, chat, agentic routing, evals, auth,
admin) behind HTTP endpoints. Inside this container everything is still in-process
imports; only the frontend -> backend hop is HTTP.

Run:  uvicorn api:app --host 0.0.0.0 --port 8000
"""
import os
import tempfile

from dotenv import load_dotenv

# Load config/secrets BEFORE importing backend modules (they read os.getenv at import).
load_dotenv(os.path.join(os.path.dirname(__file__), "config", "settings.env"))

from fastapi import FastAPI, UploadFile, File, Form, Request, Response  # noqa: E402

from backend import (auth, appsettings, audit, observability, evals,  # noqa: E402
                     backup, results_store, notify)
from backend import chat as chatqa  # noqa: E402
from backend import routing_agent  # noqa: E402
from backend.pipeline import analyze  # noqa: E402

app = FastAPI(title="Compliance Backend API", version="1.0")

auth.init_db()
appsettings.init_db()
audit.init_db()

# Settings keys the admin UI reads/writes.
_SETTINGS_KEYS = ["admin_notify_email", "notify_on_assessment", "notify_on_user_change",
                  "notify_on_login_failure", "privacy_redact_pii", "privacy_store_results",
                  "audit_retention_days"]


# ============================================================ Core (analyst)
@app.get("/health")
def health():
    return {"checks": observability.health_check()}


@app.post("/login")
async def login(req: Request):
    b = await req.json()
    role = auth.verify(b.get("username", ""), b.get("password", ""))
    return {"ok": bool(role), "role": role}


@app.post("/assess")
async def assess(file: UploadFile = File(...), entity: str = Form(""),
                 with_narrative: bool = Form(True), user: str = Form("")):
    suffix = os.path.splitext(file.filename or "")[1]
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        return analyze(tmp_path, framework=None, with_narrative=with_narrative, user_id=user or None)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@app.post("/chat")
async def chat(req: Request):
    b = await req.json()
    return {"answer": chatqa.answer(b["result"], b["question"], user_id=b.get("user"))}


@app.post("/eval")
async def eval_(req: Request):
    b = await req.json()
    res = b["result"]
    out = {"checks": evals.deterministic_checks(res)}
    if b.get("judge"):
        out["groundedness"] = evals.groundedness(res)
    return out


@app.post("/route/plan")
async def route_plan(req: Request):
    b = await req.json()
    return routing_agent.plan(b["result"], b.get("entity", "entity"))


@app.post("/route/execute")
async def route_execute(req: Request):
    b = await req.json()
    results = routing_agent.execute_plan(b["plan"], b["result"], b.get("entity", "entity"))
    return {"results": [list(r) for r in results]}


# ============================================================ Admin: users
@app.get("/users")
def users():
    return {"users": auth.list_users()}


@app.post("/users")
async def create_user(req: Request):
    b = await req.json()
    try:
        auth.create_user(b.get("username", ""), b.get("password", ""), b.get("role", "analyst"))
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.post("/users/delete")
async def delete_user(req: Request):
    b = await req.json()
    try:
        auth.delete_user(b.get("username", ""))
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ============================================================ Admin: audit
@app.get("/audit")
def audit_recent(limit: int = 300):
    return {"count": audit.count(), "rows": audit.recent(limit)}


@app.get("/audit/export")
def audit_export():
    return Response(content=audit.export_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit_log.csv"})


@app.post("/audit/purge")
async def audit_purge(req: Request):
    b = await req.json()
    removed = audit.purge(int(b.get("days", 365)))
    return {"removed": removed}


# ============================================================ Admin: metrics / settings
@app.get("/metrics")
def metrics():
    return observability.metrics()


@app.get("/settings")
def get_settings():
    return {k: appsettings.get(k) for k in _SETTINGS_KEYS}


@app.post("/settings")
async def set_settings(req: Request):
    b = await req.json()
    for k, v in b.items():
        if k in _SETTINGS_KEYS:
            appsettings.set(k, str(v))
    return {"ok": True}


@app.post("/notify/test")
def notify_test():
    ok, msg = notify.send_test()
    return {"ok": ok, "message": msg}


# ============================================================ Admin: backup
@app.get("/backup/manifest")
def backup_manifest():
    return {"manifest": backup.manifest()}


@app.get("/backup/download")
def backup_download():
    data, included = backup.create_backup()
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=compliance_backup.zip",
                             "X-Files-Included": str(len(included))})


@app.post("/backup/restore")
async def backup_restore(file: UploadFile = File(...)):
    try:
        restored = backup.restore_backup(await file.read())
        return {"ok": True, "restored": len(restored)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================ Admin: history (Cosmos)
@app.get("/history")
def history(limit: int = 50):
    return {"configured": results_store.is_configured(),
            "items": results_store.list_recent(limit)}


@app.get("/history/{item_id}")
def history_item(item_id: str):
    return results_store.get(item_id) or {"error": "not found"}
