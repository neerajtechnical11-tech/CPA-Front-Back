"""Frontend container - Streamlit UI that talks to the backend over HTTP.

Analyst flow (login, assess, chat, eval, agentic routing) + admin tabs
(users, audit, status, settings, backup, history) - every action is an HTTP call
to the backend. No secrets or Azure SDKs here.
"""
import os

import requests
import streamlit as st

import ui
from entities import ENTITIES, ENTITY_TYPES

BACKEND = os.getenv("BACKEND_API_URL", "http://localhost:8000")
TIMEOUT = 180

st.set_page_config(page_title="Compliance Gap Analyzer", layout="wide", page_icon="🛡️")
ui.inject_theme()


def api_post(path, **kwargs):
    try:
        r = requests.post(f"{BACKEND}{path}", timeout=TIMEOUT, **kwargs)
        ct = r.headers.get("content-type", "")
        return r.ok, (r.json() if ct.startswith("application/json") else {})
    except Exception as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


def api_get(path, **kwargs):
    try:
        r = requests.get(f"{BACKEND}{path}", timeout=TIMEOUT, **kwargs)
        ct = r.headers.get("content-type", "")
        return r.ok, (r.json() if ct.startswith("application/json") else r.content)
    except Exception as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- Auth
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.markdown("<h1 style='color:#A100FF'>Compliance Gap Analyzer</h1>", unsafe_allow_html=True)
    st.caption(f"Frontend container - backend at {BACKEND}")
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            ok, data = api_post("/login", json={"username": u, "password": p})
            if ok and data.get("ok"):
                st.session_state.user = {"username": u.strip(), "role": data.get("role")}
                st.rerun()
            else:
                st.error("Invalid username or password (or backend unreachable).")
    st.stop()

user = st.session_state.user
is_admin = user["role"] == "admin"

# ---------------------------------------------------------------- Sidebar
st.sidebar.header("Entity under assessment")
etype = st.sidebar.selectbox("Entity type", ENTITY_TYPES)
if etype == "Others":
    entity = st.sidebar.text_input("Entity name", placeholder="Type the entity name...")
else:
    entity = st.sidebar.selectbox(etype, ENTITIES[etype])
with_narr = st.sidebar.checkbox("Generate gap narratives", value=True)
st.sidebar.divider()
st.sidebar.success(f"Signed in as **{user['username']}** ({user['role']})")
if st.sidebar.button("Log out"):
    st.session_state.clear()
    st.rerun()

ui.top_nav(["Compliance Gap Analyzer", "Admin console" if is_admin else "Assessment"])
ui.hero("Compliance Gap Analyzer", f"2-container demo - UI calls the backend API at {BACKEND}")

labels = ["Assess", "Chat", "Eval", "Route (agentic)"]
if is_admin:
    labels += ["Users", "Audit", "Status", "Settings", "Backup", "History"]
tab = dict(zip(labels, st.tabs(labels)))

# ================================================================ Assess
with tab["Assess"]:
    uploaded = st.file_uploader("Upload the compliance document", type=["pdf", "docx", "txt", "md"])
    if uploaded and st.button("Analyze", type="primary"):
        if not entity:
            st.warning("Pick or type an entity in the sidebar first.")
            st.stop()
        with st.spinner("Assessing via backend..."):
            ok, result = api_post("/assess",
                                  files={"file": (uploaded.name, uploaded.getvalue())},
                                  data={"entity": entity, "with_narrative": str(with_narr).lower(),
                                        "user": user["username"]})
        if not ok or result.get("error"):
            st.error(f"⛔ {result.get('error', 'Backend error')}")
        else:
            st.session_state.result = result
            st.toast("Assessment complete", icon="✅")

    res = st.session_state.get("result")
    if res:
        s = res["summary"]
        st.markdown(ui.chip(f"Score {res['compliance_score']}%", ui.PURPLE)
                    + ui.chip(f"Covered {s['covered']}", ui.GREEN)
                    + ui.chip(f"Partial {s['partial']}", ui.ORANGE)
                    + ui.chip(f"Gap {s['gap']}", ui.RED), unsafe_allow_html=True)
        st.dataframe([{k: v for k, v in c.items() if k not in ("text", "evidence", "embedding")}
                      for c in res["controls"]], use_container_width=True)

# ================================================================ Chat
with tab["Chat"]:
    res = st.session_state.get("result")
    if not res:
        st.caption("Run an assessment first.")
    else:
        q = st.chat_input("Ask about controls, gaps, frameworks, risks...")
        if q:
            with st.chat_message("user"):
                st.markdown(q)
            ok, data = api_post("/chat", json={"result": res, "question": q, "user": user["username"]})
            with st.chat_message("assistant"):
                st.markdown(data.get("answer", f"Error: {data.get('error')}"))

# ================================================================ Eval
with tab["Eval"]:
    res = st.session_state.get("result")
    if not res:
        st.caption("Run an assessment first.")
    else:
        judge = st.checkbox("Include LLM groundedness judge (costs tokens)", value=True)
        if st.button("Run evaluation", type="primary"):
            with st.spinner("Evaluating via backend..."):
                ok, data = api_post("/eval", json={"result": res, "judge": judge})
            for c in data.get("checks", []):
                (st.success if c["pass"] else st.error)(f"**{c['name']}** - {c['detail']}")
            g = data.get("groundedness")
            if g:
                st.markdown(f"#### Groundedness {g['grounded']}/{g.get('judged', g['total'])} answered")
                st.dataframe(g["rows"], use_container_width=True)

# ================================================================ Route (agentic)
with tab["Route (agentic)"]:
    res = st.session_state.get("result")
    if not res:
        st.caption("Run an assessment first.")
    else:
        st.caption("Agentic routing (AGENTIC_ROUTING=true on the backend). Propose-only; nothing sends until Approve.")
        if st.button("🤖 Generate routing plan (AI)"):
            with st.spinner("Backend agent is reasoning over the gaps..."):
                ok, plan = api_post("/route/plan", json={"result": res, "entity": entity})
            st.session_state.plan = plan if ok else None
            if not ok:
                st.error(plan.get("error", "Backend error"))
        plan = st.session_state.get("plan")
        if plan:
            rows = [{"Control": r["control_id"], "Team": r["team"], "Lead": r.get("email") or "(unset)",
                     "Why": r.get("reason", "")} for r in plan.get("routes", [])]
            if rows:
                st.dataframe(rows, use_container_width=True)
            if plan.get("jira"):
                st.warning(f"🎫 Proposed Jira ticket - priority {plan['jira'].get('priority')}")
            if rows and st.button("✅ Approve & send plan", type="primary"):
                with st.spinner("Backend sending approved plan..."):
                    ok, out = api_post("/route/execute", json={"plan": plan, "result": res, "entity": entity})
                for team, email, sent, msg in out.get("results", []):
                    (st.success if sent else st.error)(f"{team} -> {email}: {msg}")

# ================================================================ ADMIN TABS
if is_admin:
    with tab["Users"]:
        st.markdown("**Create user**")
        with st.form("create_user", clear_on_submit=True):
            nu = st.text_input("New username")
            npw = st.text_input("New password", type="password")
            nrole = st.selectbox("Role", ["analyst", "admin"])
            if st.form_submit_button("Create user", type="primary"):
                ok, d = api_post("/users", json={"username": nu, "password": npw, "role": nrole})
                (st.success if d.get("ok") else st.error)(d.get("error", f"Created '{nu}'."))
                st.rerun()
        st.markdown("**Existing users**")
        ok, d = api_get("/users")
        for u in (d.get("users", []) if ok else []):
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.write(u["username"]); c2.write(u["role"])
            if c3.button("🗑", key=f"del_{u['username']}", disabled=u["username"] == user["username"]):
                ok, dd = api_post("/users/delete", json={"username": u["username"]})
                (st.success if dd.get("ok") else st.error)(dd.get("error", "Deleted."))
                st.rerun()

    with tab["Audit"]:
        ok, d = api_get("/audit", params={"limit": 300})
        if ok:
            st.markdown(f"**Audit log** - {d.get('count', 0)} events")
            st.dataframe(d.get("rows", []), use_container_width=True, height=380)
        c1, c2 = st.columns(2)
        if c1.button("Prepare CSV export"):
            ok, content = api_get("/audit/export")
            if ok:
                c1.download_button("Download audit_log.csv", content, "audit_log.csv", "text/csv")
        days = st.number_input("Purge older than (days)", 1, 3650, 365)
        if c2.button("Purge"):
            ok, dd = api_post("/audit/purge", json={"days": int(days)})
            st.success(f"Removed {dd.get('removed', 0)} entries.")

    with tab["Status"]:
        if st.button("Refresh"):
            st.rerun()
        ok, d = api_get("/health")
        for c in (d.get("checks", []) if ok else []):
            icon = {"ok": "🟢", "warn": "🟡", "error": "🔴"}.get(c["status"], "⚪")
            (st.success if c["status"] == "ok" else st.warning if c["status"] == "warn" else st.error)(
                f"{icon} **{c['component']}** - {c['detail']}")
        ok, m = api_get("/metrics")
        if ok:
            cols = st.columns(4)
            cols[0].metric("Assessments", m.get("assessments", 0))
            cols[1].metric("Sign-ins", m.get("sign_ins", 0))
            cols[2].metric("Reports", m.get("reports_downloaded", 0))
            cols[3].metric("Total events", m.get("total_events", 0))

    with tab["Settings"]:
        ok, s = api_get("/settings")
        s = s if ok else {}
        st.markdown("**Notifications & privacy**")
        with st.form("settings"):
            email = st.text_input("Admin notification email", value=s.get("admin_notify_email", "") or "")
            on_assess = st.checkbox("Notify on each assessment", value=s.get("notify_on_assessment") == "true")
            redact = st.checkbox("Redact PII before the LLM", value=s.get("privacy_redact_pii") == "true")
            store = st.checkbox("Persist results (Cosmos)", value=s.get("privacy_store_results") == "true")
            if st.form_submit_button("Save", type="primary"):
                api_post("/settings", json={"admin_notify_email": email,
                                            "notify_on_assessment": str(on_assess).lower(),
                                            "privacy_redact_pii": str(redact).lower(),
                                            "privacy_store_results": str(store).lower()})
                st.success("Saved.")
        if st.button("Send test email"):
            ok, d = api_post("/notify/test")
            (st.success if d.get("ok") else st.error)(d.get("message", ""))

    with tab["Backup"]:
        st.warning("⚠️ A backup contains secrets + password hashes. Store it securely.")
        ok, d = api_get("/backup/manifest")
        if ok:
            st.dataframe(d.get("manifest", []), use_container_width=True)
        if st.button("Prepare backup"):
            ok, content = api_get("/backup/download")
            if ok:
                st.download_button("Download compliance_backup.zip", content, "compliance_backup.zip", "application/zip")
        up = st.file_uploader("Restore from a backup .zip", type=["zip"])
        if up is not None and st.button("Restore"):
            ok, d = api_post("/backup/restore", files={"file": (up.name, up.getvalue())})
            (st.success if d.get("ok") else st.error)(d.get("error", f"Restored {d.get('restored')} files."))

    with tab["History"]:
        ok, d = api_get("/history", params={"limit": 50})
        if not (d.get("configured") if ok else False):
            st.info("Cosmos DB history is not configured (set COSMOS_* in settings.env).")
        else:
            st.dataframe(d.get("items", []), use_container_width=True)
