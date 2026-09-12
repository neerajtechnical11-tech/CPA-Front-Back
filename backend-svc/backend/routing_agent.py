"""Agentic Gap Routing (opt-in, plan-only).

When AGENTIC_ROUTING is on, an LLM reasons over the assessment's gaps using a
tool-calling loop and produces a PLAN: which team owns each gap, whether to raise
one Jira ticket, and whether to escalate. It sends nothing - a human approves the
plan, then the existing deterministic executors (agent / jira_agent / notify) act.

Exactness comes from the harness, not the model:
  - enum tool schemas constrain generation to valid teams / priorities;
  - Pydantic + allow-list validation reject anything invalid (team, control_id,
    priority) and default safely;
  - the recipient email is resolved deterministically from config (never the model);
  - the loop is bounded (max_steps) and idempotent (no duplicate routes/tickets);
  - it only PROPOSES; execution is a separate, human-approved step.
"""
import json
import os
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from backend import agent, audit, azure_openai, notify, tracing

TEAM_NAMES = [t[0] for t in agent.TEAMS]

# Max agent-loop iterations (model round-trips). Configurable via settings.env (default 24).
_MAX_STEPS = int(os.getenv("AGENTIC_MAX_STEPS", "24"))

GOAL = (
    "You are a compliance gap-routing agent. Work in this ORDER:\n"
    "1) FIRST, call route_gap for EVERY High-risk Gap/Partial control (and as many "
    "Medium/Low as steps allow), assigning each to exactly ONE owning team based on its "
    "category/title. Use the team name EXACTLY as spelled in the Available teams list. "
    "Mapping hints: network/firewall/segmentation/VPN/DNS -> Network; "
    "identity/access/authentication/PAM/MFA -> Identity & Access; "
    "application/web/API/code/SDLC -> Application; database/data/encryption/backup -> "
    "Database & Data; monitoring/logging/incident/SOC/vulnerability/patch -> "
    "Security Operations; policy/audit/compliance/governance/risk/vendor -> "
    "Governance & Risk.\n"
    "2) THEN, if several High-risk gaps belong together, call propose_jira ONCE for a single "
    "tracking ticket.\n"
    "3) THEN, if the overall posture is poor, call escalate.\n"
    "4) Do NOT call finish until every High-risk Gap/Partial control has been routed via "
    "route_gap. Only then call finish.\n"
    "Use ONLY the control IDs provided - never invent one. Available teams: "
    + ", ".join(TEAM_NAMES) + "."
)


# ----------------------------- tool schemas (Layer 1: constrain generation) --------------
def _tools():
    return [
        {"type": "function", "function": {
            "name": "route_gap",
            "description": "Assign one Gap/Partial control to an owning team.",
            "parameters": {"type": "object", "additionalProperties": False,
                "properties": {
                    "control_id": {"type": "string"},
                    "team": {"type": "string", "enum": TEAM_NAMES},
                    "reason": {"type": "string"}},
                "required": ["control_id", "team"]}}},
        {"type": "function", "function": {
            "name": "propose_jira",
            "description": "Propose ONE Jira ticket for a cluster of high-risk gaps.",
            "parameters": {"type": "object", "additionalProperties": False,
                "properties": {
                    "control_ids": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "summary": {"type": "string"}},
                "required": ["control_ids", "priority"]}}},
        {"type": "function", "function": {
            "name": "escalate",
            "description": "Flag the assessment for leadership escalation.",
            "parameters": {"type": "object", "additionalProperties": False,
                "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
        {"type": "function", "function": {
            "name": "finish", "description": "Finish and return the plan.",
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    ]


# ----------------------------- validation models (Layer 2: guarantee) --------------------
class RouteGap(BaseModel):
    control_id: str
    team: str
    reason: str = ""

    @field_validator("team")
    @classmethod
    def _team(cls, v):
        if v not in TEAM_NAMES:
            raise ValueError("invalid team")
        return v


class ProposeJira(BaseModel):
    control_ids: list[str]
    priority: Literal["P1", "P2", "P3"] = "P3"
    summary: str = ""


def _render_gaps(result) -> str:
    s = result.get("summary", {})
    lines = [f"Overall score {result.get('compliance_score')}%. "
             f"Covered {s.get('covered')}, Partial {s.get('partial')}, Gap {s.get('gap')}.",
             "Gaps/Partials to route (control_id | state | risk | category | title):"]
    for c in result.get("controls", []):
        if c.get("state") in ("Gap", "Partial"):
            lines.append(f"{c['control_id']} | {c.get('state')} | risk {c.get('risk', '-')} | "
                         f"{c.get('category', '')} | {c['title']}")
    return "\n".join(lines)


# ----------------------------- the executor (validate + stage, never send) ---------------
def _execute(name, raw_args, plan, result) -> str:
    gap_ids = {c["control_id"] for c in result.get("controls", [])
               if c.get("state") in ("Gap", "Partial")}

    if name == "route_gap":
        try:
            a = RouteGap(**raw_args)                      # shape/type/team validation
        except ValidationError as e:
            return f"rejected: {e.errors()[0]['msg']}"
        if a.control_id not in gap_ids:                  # control exists AND is a gap
            return "rejected: unknown or non-gap control_id"
        if any(r["control_id"] == a.control_id for r in plan["routes"]):
            return "already routed"                      # idempotency
        email = agent.team_email(a.team)                 # RECIPIENT from config, not the model
        plan["routes"].append({"control_id": a.control_id, "team": a.team,
                               "email": email, "reason": a.reason})
        return f"routed {a.control_id} -> {a.team}"

    if name == "propose_jira":
        try:
            a = ProposeJira(**raw_args)                   # priority enum validated
        except ValidationError as e:
            return f"rejected: {e.errors()[0]['msg']}"
        ids = [c for c in a.control_ids if c in gap_ids]  # drop fabricated ids
        if not ids:
            return "rejected: no valid gap control_ids"
        if plan["jira"]:
            return "a ticket is already proposed"         # dedup: one ticket only
        plan["jira"] = {"control_ids": ids, "priority": a.priority, "summary": a.summary[:200]}
        return f"jira proposed ({a.priority}, {len(ids)} controls)"

    if name == "escalate":
        plan["escalate"] = str(raw_args.get("reason", ""))[:300]
        return "escalation noted"

    return "finish"


# ----------------------------- the loop (bounded, traced, plan-only) ---------------------
def plan(result: dict, entity: str, max_steps: int | None = None) -> dict:
    """Reason a routing plan. Returns {routes, jira, escalate}. Sends nothing."""
    max_steps = max_steps or _MAX_STEPS
    out = {"routes": [], "jira": None, "escalate": None}
    msgs = [{"role": "system", "content": GOAL},
            {"role": "user", "content": _render_gaps(result)}]
    with tracing.task("agentic-routing", input={"entity": entity}, as_type="agent",
                      session_id=result.get("correlation_id"), tags=["gap-routing", "agentic"]):
        for _ in range(max_steps):
            resp = azure_openai.chat_tools(msgs, _tools())
            m = resp.choices[0].message
            if not getattr(m, "tool_calls", None):
                break
            msgs.append(m.model_dump(exclude_none=True))
            done = False
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                res_str = _execute(tc.function.name, args, out, result)
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res_str})
                if tc.function.name == "finish":
                    done = True
            if done:
                break
    # Deterministic safety net: LLMs don't reliably emit route_gap on every run. If the agent
    # produced no team routes, fall back to keyword routing (agent.route_gaps) so the analyst
    # always gets a usable team breakdown. The agentic proposal is preferred when present.
    out["routed_by"] = "agent"
    if not out["routes"]:
        out["routed_by"] = "keyword-fallback"
        for team, gaps in agent.route_gaps(result).items():
            email = agent.team_email(team)
            for c in gaps:
                out["routes"].append({"control_id": c["control_id"], "team": team,
                                      "email": email,
                                      "reason": "keyword-routed (deterministic fallback)"})
    # Ensure a tracking Jira ticket is proposed for a poor posture even if the model did not
    # call propose_jira; jira_agent builds the below-threshold ticket from the assessment on
    # approval. Still human-gated - nothing is created until the analyst clicks Approve.
    if not out["jira"]:
        high = [c["control_id"] for c in result.get("controls", [])
                if c.get("state") in ("Gap", "Partial") and c.get("risk") == "High"]
        if high:
            out["jira"] = {"control_ids": high, "priority": "P1",
                           "summary": "Below-threshold compliance gaps - remediation tracking"}
    tracing.decision("agentic routing plan",
                     f"{len(out['routes'])} routes ({out['routed_by']}), "
                     f"jira={bool(out['jira'])}, escalate={bool(out['escalate'])}")
    audit.record(None, "agentic_routing_plan", correlation_id=result.get("correlation_id"),
                 routes=len(out["routes"]), routed_by=out["routed_by"],
                 jira=bool(out["jira"]), escalate=bool(out["escalate"]))
    return out


# ----------------------------- execution (human-approved) --------------------------------
def execute_plan(plan_obj: dict, result: dict, entity: str):
    """ACTION (after human approval): email each team its routed gaps. Returns [(team, email, ok, msg)].

    Reuses the deterministic email path; the Jira ticket (if proposed) is created by the
    caller via jira_agent so its content stays deterministic.
    """
    by_id = {c["control_id"]: c for c in result.get("controls", [])}
    by_team: dict[str, list] = {}
    for r in plan_obj.get("routes", []):
        c = by_id.get(r["control_id"])
        if c:
            by_team.setdefault(r["team"], []).append(c)
    out = []
    for team, gaps in by_team.items():
        email = agent.team_email(team)
        if not email:
            continue
        subject = f"[Compliance] {len(gaps)} control(s) for {team} - {entity}"
        ok, msg = notify.send_email(subject, agent._compose(team, gaps, entity), to=email)
        out.append((team, email, ok, msg))
    return out
