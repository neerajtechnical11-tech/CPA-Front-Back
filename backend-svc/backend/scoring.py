"""Map retrieval similarity to coverage states, compliance score, and risk."""
import os

COVERED = float(os.getenv("COVERED_THRESHOLD", "0.75"))
PARTIAL = float(os.getenv("PARTIAL_THRESHOLD", "0.55"))

_CRIT_WEIGHT = {"High": 3, "Medium": 2, "Low": 1}


def coverage_state(similarity: float) -> str:
    if similarity >= COVERED:
        return "Covered"
    if similarity >= PARTIAL:
        return "Partial"
    return "Gap"


def risk_rating(state: str, criticality: str) -> str:
    if state == "Covered":
        return "None"
    w = _CRIT_WEIGHT.get(criticality, 2) * (2 if state == "Gap" else 1)
    return "High" if w >= 5 else "Medium" if w >= 3 else "Low"


def score_controls(findings: list[dict]) -> dict:
    """`findings`: list of {control_id, best_similarity, criticality, ...}.

    Returns per-control states plus an overall weighted compliance score.
    """
    covered = partial = 0.0
    rows = []
    for f in findings:
        state = coverage_state(f["best_similarity"])
        covered += 1 if state == "Covered" else 0
        partial += 1 if state == "Partial" else 0
        rows.append({**f, "state": state,
                     "risk": risk_rating(state, f.get("criticality", "Medium"))})
    total = len(findings) or 1
    score = round(100 * (covered + 0.5 * partial) / total, 1)
    return {"compliance_score": score, "controls": rows,
            "summary": {"total": total, "covered": int(covered),
                        "partial": int(partial),
                        "gap": total - int(covered) - int(partial)}}
