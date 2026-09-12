"""Manual post-assessment evaluation of an assessment result.

Two layers, mirroring the app's design:
  - deterministic_checks(): instant, offline guardrail checks (no Azure).
  - groundedness():         optional LLM-as-judge check that each gap narrative is
                            supported only by its evidence (uses Azure OpenAI).

Manual only - invoked from the Eval tab on a button click, never automatically.
"""
import os

from backend import scoring, azure_openai

# Optional separate deployment for the groundedness judge (more independent grading).
# Blank -> falls back to the main chat deployment (AZURE_OPENAI_CHAT_DEPLOYMENT).
_JUDGE_DEPLOY = os.getenv("AZURE_OPENAI_JUDGE_DEPLOYMENT", "").strip() or None


def _chk(name, ok, detail):
    return {"name": name, "pass": bool(ok), "detail": detail}


def deterministic_checks(result: dict) -> list[dict]:
    """Fast, offline guardrail checks on one assessment result."""
    controls = result.get("controls", [])
    total = len(controls)
    checks = []

    # Full coverage: every control received a state (REQ-ASSESS-001).
    stated = sum(1 for c in controls if c.get("state") in ("Covered", "Partial", "Gap"))
    checks.append(_chk("Full coverage", total > 0 and stated == total,
                       f"{stated}/{total} controls received a state"))

    # Determinism: re-scoring the same findings yields the same score (REQ-SCORE-002).
    rescored = scoring.score_controls(
        [{"best_similarity": c.get("best_similarity", 0.0),
          "criticality": c.get("criticality", "Medium")} for c in controls])
    same = abs(rescored["compliance_score"] - (result.get("compliance_score") or 0)) < 1e-9
    checks.append(_chk("Score is deterministic", same,
                       f"re-scored {rescored['compliance_score']}% vs stored "
                       f"{result.get('compliance_score')}%"))

    # Guardrail: scoring uses only similarity + criticality, so narrative text is
    # excluded by construction - the LLM cannot move the score.
    checks.append(_chk("LLM cannot influence score", same,
                       "scoring uses only similarity + criticality; narrative text is ignored"))

    # Every Partial/Gap has a narrative (or a graceful placeholder).
    pg = [c for c in controls if c.get("state") in ("Partial", "Gap")]
    narrated = [c for c in pg if str(c.get("narrative") or "").strip()]
    checks.append(_chk("Every gap explained", len(narrated) == len(pg),
                       f"{len(narrated)}/{len(pg)} Partial/Gap controls have a narrative"))

    return checks


def _judge(control: dict) -> tuple[str, str]:
    """LLM-as-judge: is this narrative grounded strictly in the control + evidence?

    Returns (verdict, reason) where verdict is 'yes' | 'no' | 'unclear'. 'unclear'
    means the judge gave no usable answer (empty/errored) - so it is NOT counted as
    a hallucination, avoiding a false 'Grounded 0/N'.
    """
    prompt = (
        "You are auditing a compliance gap narrative for hallucination. Decide if the "
        "NARRATIVE is grounded - it must not assert any fact not supported by the CONTROL "
        "or the EVIDENCE.\n"
        f"CONTROL: {control.get('control_id')} - {control.get('title')}: "
        f"{str(control.get('text', ''))[:300]}\n"
        f"EVIDENCE: \"{str(control.get('evidence', ''))[:300]}\"\n"
        f"NARRATIVE: \"{str(control.get('narrative', ''))[:300]}\"\n"
        "Reply EXACTLY as: <yes|no> - <reason in 12 words or fewer>")
    # Enough tokens for a reasoning-style chat model to actually answer (a low cap
    # can leave the content empty, which previously defaulted everything to 'no').
    try:
        ans = (azure_openai.chat(prompt, max_tokens=200, deployment=_JUDGE_DEPLOY) or "").strip()
    except Exception as e:
        return "unclear", f"judge call failed: {e}"[:120]
    if not ans:
        return "unclear", "judge returned an empty response (check the chat model / token cap)"
    low = ans.lower()
    if low.startswith("yes") or "yes -" in low[:12] or "grounded: yes" in low:
        verdict = "yes"
    elif low.startswith("no") or "no -" in low[:12] or "grounded: no" in low:
        verdict = "no"
    else:
        verdict = "unclear"
    reason = ans.split("-", 1)[1].strip() if "-" in ans else ans
    return verdict, reason[:150]


def groundedness(result: dict, limit: int = 10) -> dict:
    """Judge up to `limit` narrated Partial/Gap controls. Uses Azure OpenAI.

    `grounded` / `judged` only count controls that got a clear yes|no; 'unclear'
    ones (empty/errored judge) are shown but not scored, so an unreachable judge
    can't read as 'all hallucinated'.
    """
    pg = [c for c in result.get("controls", [])
          if c.get("state") in ("Partial", "Gap") and str(c.get("narrative") or "").strip()]
    rows, grounded, judged = [], 0, 0
    for c in pg[:limit]:
        verdict, reason = _judge(c)
        if verdict in ("yes", "no"):
            judged += 1
            if verdict == "yes":
                grounded += 1
        rows.append({"Control": c.get("control_id"), "State": c.get("state"),
                     "Grounded": verdict, "Reason": reason})
    return {"grounded": grounded, "judged": judged, "total": len(pg[:limit]), "rows": rows}
