"""Backup & restore of local application state (zip archive).

What is stateful (and therefore backed up):
  - auth.db            users, runtime settings (appsettings), audit log
  - license.key        installed license
  - .license_state.json license clock-rollback high-water mark
  - config/settings.env configuration & secrets (SENSITIVE)
  - .streamlit/secrets.toml Entra SSO config, if used (SENSITIVE)
  - ingestion/baselines/    baseline control-register sources

Derived / external (NOT in the backup): the Azure AI Search index is rebuilt from
the baseline sources via ingestion; uploaded documents and results are not persisted.

Note: a backup contains secrets and password hashes - store it securely / encrypted.
"""
import datetime as dt
import io
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (relative path, kind, description, sensitive?)
ITEMS = [
    ("auth.db", "file", "Users, runtime settings, audit log", False),
    ("license.key", "file", "Installed license", False),
    (".license_state.json", "file", "License clock state", False),
    ("config/settings.env", "file", "Configuration & secrets", True),
    (".streamlit/secrets.toml", "file", "Entra SSO config (if used)", True),
    ("ingestion/baselines", "dir", "Baseline control-register sources", False),
]


def _size(p: str) -> int:
    if os.path.isdir(p):
        return sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _, fs in os.walk(p) for f in fs)
    return os.path.getsize(p)


def manifest() -> list[dict]:
    out = []
    for rel, kind, desc, sensitive in ITEMS:
        p = os.path.join(ROOT, rel)
        present = os.path.exists(p)
        out.append({"path": rel, "kind": kind, "description": desc,
                    "sensitive": sensitive, "present": present,
                    "bytes": _size(p) if present else 0})
    return out


def create_backup() -> tuple[bytes, list[str]]:
    """Build an in-memory zip of all present state; returns (zip_bytes, included)."""
    buf = io.BytesIO()
    included = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, kind, desc, sensitive in ITEMS:
            p = os.path.join(ROOT, rel)
            if not os.path.exists(p):
                continue
            if os.path.isdir(p):
                for dp, _, fs in os.walk(p):
                    for f in fs:
                        full = os.path.join(dp, f)
                        arc = os.path.relpath(full, ROOT).replace("\\", "/")
                        z.write(full, arc)
                        included.append(arc)
            else:
                z.write(p, rel.replace("\\", "/"))
                included.append(rel.replace("\\", "/"))
        meta = {"created": dt.datetime.now().isoformat(timespec="seconds"),
                "app": "compliance-gap-analyzer", "items": included}
        z.writestr("backup_manifest.json", json.dumps(meta, indent=2))
    buf.seek(0)
    return buf.getvalue(), included


def restore_backup(data: bytes) -> list[str]:
    """Restore files from a backup zip into the project root (overwrites).

    Includes zip-slip protection: entries resolving outside ROOT are skipped.
    """
    restored = []
    root_norm = os.path.normpath(ROOT)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name == "backup_manifest.json" or name.endswith("/"):
                continue
            dest = os.path.normpath(os.path.join(ROOT, name))
            if dest != root_norm and not dest.startswith(root_norm + os.sep):
                continue  # zip-slip attempt - skip
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                out.write(src.read())
            restored.append(name)
    return restored
