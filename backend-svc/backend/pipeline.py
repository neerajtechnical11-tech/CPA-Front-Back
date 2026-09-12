"""End-to-end analysis: uploaded document -> compliance score + risk findings.

Full-coverage strategy: pull every baseline control (with its stored embedding)
from Azure AI Search, then for each control take the MAX cosine similarity across
the uploaded document's chunks. This scores every control (not only ones that
happen to surface in a top-k search), so gaps are enumerated completely.
"""
import hashlib
import os

import numpy as np

from backend import ai_search_client, audit, azure_openai, narrative, privacy, runtrace, scoring, tracing
from backend.parser import chunk, extract_text


def _cosine_matrix(controls: np.ndarray, chunks: np.ndarray) -> np.ndarray:
    """Return (n_controls, n_chunks) cosine similarity matrix."""
    c = controls / (np.linalg.norm(controls, axis=1, keepdims=True) + 1e-9)
    d = chunks / (np.linalg.norm(chunks, axis=1, keepdims=True) + 1e-9)
    return c @ d.T


def analyze(file_path: str, framework: str | None = None, with_narrative: bool = True,
            tracker=None, user_id: str | None = None) -> dict:
    """Run one assessment. Pass a runtrace.RunTracker to record per-stage status
    (used by the Status Analysis tab); defaults to a no-op tracker.

    The whole run is a single Langfuse 'task' trace (when tracing is configured),
    with a span per stage (Tool Calls), the Azure OpenAI embed/narrate steps as
    Model Calls, the scoring thresholds as a Decision Branch, and the score as the
    task Outcome - so per-assessment cost and flow are visible in the dashboard.
    The correlation id doubles as the Langfuse session id so the follow-on chat /
    routing / ticket traces for this assessment group together."""
    cid = audit.new_correlation_id()
    with tracing.task("assessment",
                      input={"document": os.path.basename(file_path), "framework": framework},
                      user_id=user_id, session_id=cid, tags=["assessment"]) as _task:
        result = _run(file_path, framework, with_narrative, tracker, cid)
        s = result.get("summary", {})
        _task.update(output={"compliance_score": result.get("compliance_score"),
                             "covered": s.get("covered"), "partial": s.get("partial"),
                             "gap": s.get("gap"), "correlation_id": result.get("correlation_id")})
        return result


def _run(file_path: str, framework: str | None, with_narrative: bool, tracker, cid: str) -> dict:
    tr = tracker or runtrace.NULL

    # Traceability: one correlation id per assessment (also the Langfuse session id)
    # + a hash of the source document.
    try:
        with open(file_path, "rb") as f:
            doc_sha256 = hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        doc_sha256 = "-"

    with tr.step("1", "Extract & chunk document", "backend/parser.py",
                 "extract_text() / chunk()") as rec, \
            tracing.step("extract & chunk document", "tool"):
        text = extract_text(file_path)
        chunks = chunk(text)
        if not chunks:
            raise ValueError("No readable text found in the document. It may be empty, "
                             "an image-only scan, or contain no extractable text.")
        rec["detail"] = f"{len(chunks)} chunks (120-word window)"

    with tr.step("2", "Embed chunks (Azure OpenAI)", "backend/azure_openai.py",
                 "embed_batch()") as rec, \
            tracing.step("embed chunks", "chain"):
        chunk_vecs = np.array(azure_openai.embed_batch(chunks), dtype=np.float32)
        dim = chunk_vecs.shape[1] if chunk_vecs.ndim > 1 else 0
        rec["detail"] = f"{len(chunks)} vectors, {dim}-dim"

    with tr.step("3", "Fetch baseline controls (Azure AI Search)",
                 "backend/ai_search_client.py", "get_all_controls()") as rec, \
            tracing.step("fetch baseline controls", "retriever",
                         input={"framework": framework}):
        controls = ai_search_client.get_all_controls(framework)
        if not controls:
            raise RuntimeError("No baseline controls found. Run ingestion/ingest_register.py first.")
        rec["detail"] = f"{len(controls)} controls with vectors"

    with tr.step("4-5", "Similarity + best-match evidence", "backend/pipeline.py",
                 "_cosine_matrix() / argmax") as rec, \
            tracing.step("similarity + best-match evidence", "tool"):
        ctrl_vecs = np.array([c["embedding"] for c in controls], dtype=np.float32)
        sims = _cosine_matrix(ctrl_vecs, chunk_vecs)  # (controls, chunks)
        findings = []
        for i, c in enumerate(controls):
            best_chunk = int(np.argmax(sims[i]))
            findings.append({
                "framework": c["framework"],
                "control_id": c["control_id"],
                "title": c["title"],
                "text": c["text"],
                "criticality": c.get("criticality", "Medium"),
                "nist_csf": c.get("nist_csf", ""),
                "iso_27002": c.get("iso_27002", ""),
                "nca_ecc": c.get("nca_ecc", ""),
                "nist_800_53": c.get("nist_800_53", ""),
                "cis": c.get("cis", ""),
                "best_similarity": float(sims[i, best_chunk]),
                "evidence": chunks[best_chunk],
            })
        rec["detail"] = f"{len(controls)} x {len(chunks)} cosine matrix"

    with tr.step("6", "Score controls (thresholds)", "backend/scoring.py",
                 "score_controls()") as rec, \
            tracing.step("score controls", "chain"):
        result = scoring.score_controls(findings)
        s = result["summary"]
        rec["detail"] = (f"score {result['compliance_score']}% - "
                         f"Covered {s['covered']} / Partial {s['partial']} / Gap {s['gap']}")
        # Decision Branch: deterministic thresholds map similarity -> Covered/Partial/Gap.
        tracing.decision("scoring thresholds", f"{result['compliance_score']}% compliant",
                         metadata={"covered_threshold": os.getenv("COVERED_THRESHOLD"),
                                   "partial_threshold": os.getenv("PARTIAL_THRESHOLD"),
                                   "covered": s["covered"], "partial": s["partial"],
                                   "gap": s["gap"], "total": s.get("total")})

    narrated = 0
    if with_narrative:
        with tr.step("7-8", "Generate gap recommendations (LLM)", "backend/narrative.py",
                     "explain_gap() -> chat()") as rec, \
                tracing.step("generate gap recommendations", "chain"):
            failed = 0
            for row in result["controls"]:
                if row["state"] in ("Partial", "Gap"):
                    # Privacy control: redact PII from the excerpt before it is sent to the LLM.
                    evidence = privacy.redact_if_enabled(row["evidence"])
                    # Resilience: a narrative is a "nice-to-have". If the LLM is
                    # unreachable (retries exhausted / circuit open), skip it with a
                    # placeholder so the deterministic score still returns.
                    try:
                        row["narrative"] = narrative.explain_gap(row, evidence, row["state"])
                        narrated += 1
                    except Exception:
                        row["narrative"] = "Narrative unavailable - the model was temporarily unreachable."
                        failed += 1
            rec["detail"] = (f"{narrated} recommendations written"
                             + (f", {failed} skipped (model unreachable)" if failed else ""))
    else:
        tr.skip("7-8", "Generate gap recommendations (LLM)", "backend/narrative.py",
                "explain_gap()", "narratives disabled for this run")

    # Traceability: record the assessment with provenance so it can be reconstructed.
    result["correlation_id"] = cid
    s = result.get("summary", {})
    audit.record(None, "assessment", correlation_id=cid,
                 document_sha256=doc_sha256,
                 chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
                 embed_deployment=os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT"),
                 api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
                 covered_threshold=os.getenv("COVERED_THRESHOLD"),
                 partial_threshold=os.getenv("PARTIAL_THRESHOLD"),
                 score=result.get("compliance_score"),
                 total=s.get("total"), covered=s.get("covered"),
                 partial=s.get("partial"), gap=s.get("gap"),
                 narratives=narrated)
    return result
