"""Analyst chat over the latest assessment result.

Answers questions about the controls in the document just analyzed - e.g.
"show me the missing controls from the NIST framework". Common, structured
questions (missing/partial/covered, by framework, by risk, a specific control,
the overall score) are answered deterministically from the assessment data for
speed and accuracy. Anything free-form falls back to the same Azure OpenAI chat
model, grounded strictly in the findings so it can't invent controls.
"""
import re
from backend import audit, azure_openai, tracing

# Guardrail: cap free-form (LLM) chat answers so token usage stays under control.
MAX_ANSWER_WORDS = 25
MAX_ANSWER_TOKENS = 60          # ~ enough for 25 words plus a cited control id


def _cap_words(text, n=MAX_ANSWER_WORDS):
    words = text.split()
    if len(words) <= n:
        return text
    return " ".join(words[:n]).rstrip(".,;:") + " ..."


# question keyword -> coverage state
_STATE_WORDS = [
    ("not covered", "Gap"), ("missing", "Gap"), ("uncovered", "Gap"),
    ("gap", "Gap"), ("failing", "Gap"), ("failed", "Gap"),
    ("partial", "Partial"),
    ("covered", "Covered"), ("met", "Covered"), ("satisfied", "Covered"),
    ("compliant", "Covered"), ("passed", "Covered"),
]

# framework tokens (specific first, generic 'nist' last) -> (match-key, label)
_FRAMEWORKS = [
    (("nist csf", "csf"), "nist_csf", "NIST CSF"),
    (("800-53", "800 53", "nist 800", "sp 800"), "nist_800_53", "NIST 800-53"),
    (("iso 27002", "iso", "27002"), "iso_27002", "ISO 27002"),
    (("nca", "ecc"), "nca_ecc", "NCA ECC"),
    (("cis",), "cis", "CIS"),
    (("nist",), "nist_any", "NIST"),
    (("nbcc", "kuwait"), "framework", "Kuwait National Basic Cybersecurity Controls"),
]


def _detect_framework(ql):
    for tokens, key, label in _FRAMEWORKS:
        if any(t in ql for t in tokens):
            return key, label
    return None


def _detect_state(ql):
    for word, state in _STATE_WORDS:
        if word in ql:
            return state
    return None


def _fw_match(c, key):
    if key == "nist_any":
        return bool(c.get("nist_csf") or c.get("nist_800_53"))
    if key == "framework":
        return True  # baseline framework (all rows)
    return bool(c.get(key))


def _mappings(c):
    out = []
    for k, lab in [("nist_csf", "NIST CSF"), ("nist_800_53", "NIST 800-53"),
                   ("iso_27002", "ISO 27002"), ("nca_ecc", "NCA ECC"), ("cis", "CIS")]:
        if c.get(k):
            out.append(f"{lab}: {c[k]}")
    return "; ".join(out)


def _fmt_list(rows, label, limit=50):
    if not rows:
        return f"No {label} found in this assessment."
    order = {"Gap": 0, "Partial": 1, "Covered": 2}
    rows = sorted(rows, key=lambda c: (order.get(c.get("state"), 3),
                                       {"High": 0, "Medium": 1, "Low": 2}.get(c.get("risk"), 3)))
    lines = [f"**{len(rows)} {label}:**"]
    for c in rows[:limit]:
        m = _mappings(c)
        lines.append(f"- **{c['control_id']}** - {c['title']} - *{c.get('state','')}*, "
                     f"risk {c.get('risk', '-')}" + (f" ({m})" if m else ""))
    if len(rows) > limit:
        lines.append(f"...and {len(rows) - limit} more (see the report for the full list).")
    return "\n".join(lines)


def _summary(result):
    s = result["summary"]
    return (f"**Overall compliance score: {result['compliance_score']}%.** "
            f"Of {s['total']} controls: **{s['covered']} Covered**, "
            f"**{s['partial']} Partially applied**, **{s['gap']} Gaps**.")


def _detail(c):
    lines = [f"**{c['control_id']} - {c['title']}**",
             f"- State: **{c.get('state','')}** (similarity "
             f"{round(c.get('best_similarity', 0), 3)}), risk {c.get('risk', '-')}, "
             f"criticality {c.get('criticality', '-')}",
             f"- Requirement: {str(c.get('text',''))[:400]}"]
    m = _mappings(c)
    if m:
        lines.append(f"- Mappings: {m}")
    if c.get("narrative"):
        lines.append(f"- Gap narrative: {c['narrative']}")
    return "\n".join(lines)


def _llm(result, question):
    ctx = []
    for c in result["controls"]:
        ctx.append(f"{c['control_id']} | {c.get('state','')} | risk {c.get('risk','-')} | "
                   f"{c['title']} | NISTCSF:{c.get('nist_csf','')} 800-53:{c.get('nist_800_53','')} "
                   f"ISO:{c.get('iso_27002','')} CIS:{c.get('cis','')} NCA:{c.get('nca_ecc','')}")
    s = result["summary"]
    prompt = (
        "You are a compliance analyst assistant. Answer the user's question using ONLY the "
        "assessment data below. If the answer is not in the data, say you do not have that "
        f"information. Answer in {MAX_ANSWER_WORDS} words or fewer and cite control IDs.\n\n"
        f"Overall score {result['compliance_score']}%. Covered {s['covered']}, "
        f"Partial {s['partial']}, Gap {s['gap']} of {s['total']}.\n"
        "Controls (id | state | risk | title | framework mappings):\n"
        + "\n".join(ctx) + f"\n\nQuestion: {question}\nAnswer:")
    # Guardrail: low token cap + hard word-cap backstop (the model may not obey exactly).
    return _cap_words(azure_openai.chat(prompt, max_tokens=MAX_ANSWER_TOKENS))


def _answer(result, question):
    """Return (answer_text, route_label). route_label names the Decision Branch the
    question took: a deterministic lookup, or the grounded-LLM fallback."""
    controls = result.get("controls", [])
    if not controls:
        return "No assessment is loaded yet. Run an analysis first, then ask me about it.", "empty"
    ql = question.lower().strip()

    # a specific control id, e.g. "GOV-1"
    ids = {c["control_id"].lower(): c for c in controls}
    for tok in re.findall(r"[A-Za-z]{1,6}-?\d+[\w.\-]*", question):
        if tok.lower() in ids:
            return _detail(ids[tok.lower()]), "deterministic:control-lookup"

    if any(w in ql for w in ("score", "summary", "overall", "posture", "how compliant", "how did we do")):
        return _summary(result), "deterministic:summary"

    if any(w in ql for w in ("high risk", "high-risk", "critical", "top risk", "biggest risk", "priorit")):
        return _fmt_list([c for c in controls if c.get("risk") == "High"], "high-risk controls"), \
            "deterministic:high-risk"

    fw = _detect_framework(ql)
    state = _detect_state(ql)
    if fw or state:
        rows = controls
        if fw:
            rows = [c for c in rows if _fw_match(c, fw[0])]
        if state:
            rows = [c for c in rows if c.get("state") == state]
        label = ((state + " ") if state else "") + ((fw[1] + " ") if fw else "") + "controls"
        return _fmt_list(rows, label.strip()), "deterministic:filter"

    # free-form -> grounded LLM
    return _llm(result, question), "llm-fallback"


def answer(result, question, user_id=None):
    """Public entry: answer the question, and audit-log it (traceability)."""
    with tracing.task("analyst-chat", input={"question": str(question)[:200]},
                      as_type="agent", user_id=user_id,
                      session_id=result.get("correlation_id"), tags=["analyst-chat"]) as _task:
        resp, route = _answer(result, question)
        # Decision Branch: deterministic answer vs grounded-LLM fallback.
        tracing.decision("chat routing", route)
        _task.update(output=str(resp)[:500], metadata={"route": route})
        audit.record(None, "chat", correlation_id=result.get("correlation_id"),
                     question=str(question)[:200], answer_chars=len(resp or ""))
        return resp
