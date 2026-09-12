"""One-time (idempotent) load of baseline controls into Azure AI Search.

Reads every *.json file in ingestion/baselines/, embeds each control's text with
Azure OpenAI, and upserts into the vector index.

Usage:
    python -m ingestion.ingest
"""
import glob
import json
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "config", "settings.env"))

from backend import ai_search_client, azure_openai  # noqa: E402

BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baselines")


def safe_key(text: str) -> str:
    """Azure AI Search doc keys allow only letters, digits, _ - =."""
    return re.sub(r"[^A-Za-z0-9_\-=]", "_", text)


def load_records() -> list[dict]:
    records = []
    for path in glob.glob(os.path.join(BASELINE_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            records.extend(json.load(f))
    return records


def main():
    records = load_records()
    if not records:
        print("No baseline records found in", BASELINE_DIR)
        sys.exit(1)

    print(f"Creating index (if needed) and embedding {len(records)} controls...")
    ai_search_client.create_index()

    texts = [f"{r['title']}. {r['text']}" for r in records]
    vectors = azure_openai.embed_batch(texts)

    docs = []
    for r, v in zip(records, vectors):
        docs.append({
            "id": safe_key(f"{r['framework']}_{r['control_id']}"),
            "framework": r["framework"],
            "control_id": r["control_id"],
            "title": r["title"],
            "text": r["text"],
            "category": r.get("category", ""),
            "criticality": r.get("criticality", "Medium"),
            "embedding": v,
        })

    ai_search_client.upload_documents(docs)
    print(f"Uploaded {len(docs)} controls to index '{os.getenv('AZURE_SEARCH_INDEX')}'.")


if __name__ == "__main__":
    main()
