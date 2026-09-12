"""Gap Routing Agent - decides the owning team for each gap and emails the leads.

The agent's decision: for every Gap / Partial control, assign an owning team based
on the control's category/title (keyword routing first; optional AI assist for
anything unmatched). Its action: email each team's lead a summary of their items.

Team lead emails come from config/settings.env (LEAD_* keys); if a team's lead is
unset it falls back to LEAD_DEFAULT, then the admin notification email.
"""
import os
from backend import audit, azure_openai, notify, tracing

# team -> (routing keywords, settings.env key for the lead email)
TEAMS = [
    ("Identity & Access", ["identity", "access", "iam", "authentication", "authorization",
                            "account", "privileged", "pam", "pki", "credential", "directory",
                            "sso", "mfa", "joiner", "leaver", "role"], "LEAD_IDENTITY"),
    ("Network", ["network", "firewall", "router", "switch", "connectivity", "segmentation",
                 "vpn", "dns", "perimeter", "wireless", "routing", "traffic", "boundary"], "LEAD_NETWORK"),
    ("Application", ["application", "software", "secure development", "sdlc", "api", "web",
                     "source code", "dependency", "supply chain", "change management"], "LEAD_APPLICATION"),
    ("Database & Data", ["database", "data classification", "encryption", "backup", "storage",
                         "records", "retention", "dlp", "information", "data sovereignty"], "LEAD_DATABASE"),
    ("Security Operations", ["security operation", "soc", "monitoring", "logging", "incident",
                             "threat", "detection", "siem", "vulnerability", "patch", "malware",
                             "event", "response"], "LEAD_SECURITY"),
    ("Governance & Risk", ["governance", "policy", "risk", "audit", "compliance", "awareness",
                           "training", "third party", "vendor", "asset", "physical", "exception"], "LEAD_GRC"),
]
DEFAULT_TEAM = "Security Operations"
_ENV = {name: env for name, _kw, env in TEAMS}


def team_email(team: str) -> str:
    env = _ENV.get(team)
    e = (os.getenv(env, "").strip() if env else "")
    return e or os.getenv("LEAD_DEFAULT", "").strip() or notify.admin_email()


def _route_keyword(control) -> str | None:
    hay = " ".join(str(control.get(k, "")) for k in ("category", "title", "text")).lower()
    for name, kws, _ in TEAMS:
        if any(k in hay for k in kws):
            return name
    return None


def _route_llm(control) -> str:
    teams = ", ".join(t[0] for t in TEAMS)
    prompt = (f"Assign this security control gap to exactly ONE owning team from this list: {teams}. "
              f"Answer with only the team name.\n"
              f"Control {control.get('control_id')} - {control.get('title')}: "
              f"{str(control.get('text',''))[:200]}\nTeam:")
    try:
        ans = azure_openai.chat(prompt, max_tokens=15).strip().lower()
        for name, _, _ in TEAMS:
            if name.lower() in ans:
                return name
    except Exception:
        pass
    return DEFAULT_TEAM


def route_gaps(result: dict, use_llm_fallback: bool = False) -> dict:
    """Decide the owning team for every Gap / Partial. Returns {team: [controls]}."""
    groups: dict[str, list] = {}
    methods: dict[str, str] = {}   # control_id -> how the team was decided (traceability)
    for c in result.get("controls", []):
        if c.get("state") not in ("Gap", "Partial"):
            continue
        kw = _route_keyword(c)
        if kw:
            team, how = kw, "keyword"
        elif use_llm_fallback:
            team, how = _route_llm(c), "llm"
        else:
            team, how = DEFAULT_TEAM, "default"
        methods[c.get("control_id")] = how
        groups.setdefault(team, []).append(c)

    # Traceability: record the agent's routing decision (team + method per control).
    audit.record(None, "gap_routing", correlation_id=result.get("correlation_id"),
                 routing={team: [c.get("control_id") for c in gaps] for team, gaps in groups.items()},
                 methods=methods)
    # Decision Branch: how each gap was routed (keyword vs LLM fallback vs default).
    method_counts = {}
    for how in methods.values():
        method_counts[how] = method_counts.get(how, 0) + 1
    tracing.decision("gap routing", f"{len(groups)} team(s), methods {method_counts}",
                     metadata={"teams": {t: len(g) for t, g in groups.items()},
                               "methods": method_counts, "llm_fallback": use_llm_fallback})
    return groups


def preview(result: dict, use_llm_fallback: bool = False):
    """Return (rows-for-table, groups) so the analyst can review before sending."""
    groups = route_gaps(result, use_llm_fallback)
    rows = []
    for team, gaps in groups.items():
        email = team_email(team)
        for c in gaps:
            rows.append({"Control": c["control_id"], "Title": c["title"],
                         "State": c.get("state", ""), "Risk": c.get("risk", ""),
                         "Owning team": team, "Lead email": email or "(not set)"})
    return rows, groups


def _compose(team: str, gaps: list, entity: str) -> str:
    lines = [f"Owning team: {team}",
             f"Entity assessed: {entity}",
             f"{len(gaps)} control(s) require your team's attention:", ""]
    for c in sorted(gaps, key=lambda x: {"Gap": 0, "Partial": 1}.get(x.get("state"), 2)):
        maps = "; ".join(f"{lab}:{c.get(k)}" for k, lab in
                         [("nist_csf", "NIST CSF"), ("iso_27002", "ISO 27002"), ("cis", "CIS")] if c.get(k))
        lines.append(f"- [{c.get('state')}, risk {c.get('risk','-')}] {c['control_id']} - {c['title']}")
        if c.get("narrative"):
            lines.append(f"    Gap: {c['narrative']}")
        if maps:
            lines.append(f"    Mapped controls: {maps}")
    lines += ["", "Please review and remediate the items above.",
              "- Sent automatically by the Compliance Gap Analyzer routing agent."]
    return "\n".join(lines)


def send_to_leads(result: dict, entity: str, use_llm_fallback: bool = False, user_id=None):
    """ACTION: email each team's lead their gaps. Returns [(team, email, ok, msg)]."""
    with tracing.task("gap-routing-email", input={"entity": entity},
                      as_type="agent", user_id=user_id,
                      session_id=result.get("correlation_id"), tags=["gap-routing"]) as _task:
        groups = route_gaps(result, use_llm_fallback)
        out = []
        for team, gaps in groups.items():
            email = team_email(team)
            if not email:
                # No lead email configured for this team - skip silently instead of
                # reporting a failure row (only teams with a real address are emailed).
                continue
            subject = f"[Compliance] {len(gaps)} control(s) for {team} - {entity}"
            # Tool Call: send the team lead their gap summary via SMTP.
            with tracing.step("send email", "tool",
                              input={"team": team, "to": email, "gaps": len(gaps)}) as sp:
                ok, msg = notify.send_email(subject, _compose(team, gaps, entity), to=email)
                sp.update(output={"ok": ok, "detail": msg},
                          level=None if ok else "ERROR", status_message=None if ok else str(msg)[:200])
            out.append((team, email, ok, msg))
        sent = sum(1 for _t, _e, ok, _m in out if ok)
        _task.update(output={"teams": len(out), "sent": sent})
        return out
