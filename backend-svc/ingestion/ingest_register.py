"""Load a control-register Excel (e.g. Kuwait National Basic Cybersecurity Controls Register) into Azure AI Search.

Each control row becomes one searchable baseline record. The embedding is built
from Title + Purpose + Minimum Requirement (the auditable expectation), which is
what uploaded client documents are scored against.

Usage:
    python -m ingestion.ingest_register                 # append to the index
    python -m ingestion.ingest_register --reset         # wipe index first (clean baseline)
    python -m ingestion.ingest_register --file path.xlsx --sheet "Control Register v4"
"""
import argparse
import os
import re
import sys

from dotenv import load_dotenv
from openpyxl import load_workbook

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "config", "settings.env"))

from backend import ai_search_client, azure_openai  # noqa: E402

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "baselines",
                            "Kuwait_NBCC_Control_Register_v4.xlsx")
DEFAULT_SHEET = "Control Register v4"
FRAMEWORK = "Kuwait National Basic Cybersecurity Controls"

# Priority -> criticality (drives risk severity when a control is a gap)
PRIORITY_TO_CRIT = {"P0": "High", "P1": "Medium", "P2": "Low"}


def safe_key(text: str) -> str:
    """Azure AI Search doc keys allow only letters, digits, _ - =."""
    return re.sub(r"[^A-Za-z0-9_\-=]", "_", text)


def col_index(header: list[str], *names: str) -> int | None:
    """Find a column by any of several candidate header names (case-insensitive, contains)."""
    low = [(h or "").strip().lower() for h in header]
    for name in names:
        n = name.lower()
        for i, h in enumerate(low):
            if h == n or n in h:
                return i
    return None


def load_records(path: str, sheet: str) -> list[dict]:
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c) if c is not None else "" for c in rows[0]]

    ci = {
        "id": col_index(header, "Control ID"),
        "title": col_index(header, "Control Title", "Title"),
        "family": col_index(header, "Function / Family", "Function", "Family"),
        "priority": col_index(header, "Priority"),
        "tier": col_index(header, "Tier Applicability", "Tier"),
        "purpose": col_index(header, "Purpose / Description", "Purpose"),
        "minreq": col_index(header, "Minimum Requirement"),
        # cross-framework crosswalk columns
        "nist_csf": col_index(header, "NIST CSF"),
        "iso_27002": col_index(header, "ISO 27002"),
        "nca_ecc": col_index(header, "NCA ECC"),
        "nist_800_53": col_index(header, "800-53"),
        "cis": col_index(header, "CIS Controls", "CIS"),
    }
    if ci["id"] is None or ci["minreq"] is None:
        raise SystemExit(f"Could not find 'Control ID' / 'Minimum Requirement' columns in {sheet}. "
                         f"Headers seen: {header}")

    def cell(row, key):
        idx = ci[key]
        return ("" if idx is None or row[idx] is None else str(row[idx])).strip()

    records = []
    for row in rows[1:]:
        cid = cell(row, "id")
        if not cid:
            continue
        priority = cell(row, "priority").upper()
        records.append({
            "control_id": cid,
            "title": cell(row, "title"),
            "category": cell(row, "family"),
            "priority": priority,
            "tier": cell(row, "tier"),
            "purpose": cell(row, "purpose"),
            "min_req": cell(row, "minreq"),
            "criticality": PRIORITY_TO_CRIT.get(priority, "Medium"),
            "nist_csf": cell(row, "nist_csf"),
            "iso_27002": cell(row, "iso_27002"),
            "nca_ecc": cell(row, "nca_ecc"),
            "nist_800_53": cell(row, "nist_800_53"),
            "cis": cell(row, "cis"),
        })
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--reset", action="store_true", help="delete the index before loading")
    args = ap.parse_args()

    records = load_records(args.file, args.sheet)
    if not records:
        print("No control rows found.")
        sys.exit(1)
    print(f"Read {len(records)} controls from {os.path.basename(args.file)}.")

    if args.reset:
        print("Resetting index...")
        ai_search_client.delete_index()
    ai_search_client.create_index()

    # Embed Title + Purpose + Minimum Requirement (the auditable expectation).
    texts = [f"{r['title']}. {r['purpose']} {r['min_req']}".strip() for r in records]
    print("Embedding controls via Azure OpenAI...")
    vectors = azure_openai.embed_batch(texts)

    docs = []
    for r, v in zip(records, vectors):
        # Store the requirement as `text` (this is shown as evidence-target and embedded).
        body = f"[{r['priority']} | Tier: {r['tier']}] {r['purpose']} Minimum requirement: {r['min_req']}"
        docs.append({
            "id": safe_key(f"{FRAMEWORK}_{r['control_id']}"),
            "framework": FRAMEWORK,
            "control_id": r["control_id"],
            "title": r["title"],
            "text": body.strip(),
            "category": r["category"],
            "criticality": r["criticality"],
            "nist_csf": r["nist_csf"],
            "iso_27002": r["iso_27002"],
            "nca_ecc": r["nca_ecc"],
            "nist_800_53": r["nist_800_53"],
            "cis": r["cis"],
            "embedding": v,
        })

    ai_search_client.upload_documents(docs)
    print(f"Uploaded {len(docs)} '{FRAMEWORK}' controls to index "
          f"'{os.getenv('AZURE_SEARCH_INDEX')}'.")


if __name__ == "__main__":
    main()
