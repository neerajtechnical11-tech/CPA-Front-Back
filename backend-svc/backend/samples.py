"""Sample documents from Azure Blob Storage - lets the analyst pick a built-in
sample instead of uploading one. Safe no-op when unconfigured (mirrors
results_store / notify), so the app runs fine without it.

config/settings.env:
  SAMPLES_STORAGE_ACCOUNT     e.g. cyberaisamples
  SAMPLES_CONTAINER           e.g. samples (default)
  SAMPLES_CONNECTION_STRING   optional - a full connection string. If blank, uses
                              the account URL + DefaultAzureCredential (managed identity).
"""
import os
from functools import lru_cache

_ACCOUNT = os.getenv("SAMPLES_STORAGE_ACCOUNT", "").strip()
_CONTAINER = os.getenv("SAMPLES_CONTAINER", "samples").strip() or "samples"
_CONN = os.getenv("SAMPLES_CONNECTION_STRING", "").strip()


def is_configured() -> bool:
    """True if either a connection string or a storage account name is set."""
    return bool(_CONN or _ACCOUNT)


@lru_cache(maxsize=1)
def _container_client():
    from azure.storage.blob import BlobServiceClient
    if _CONN:
        svc = BlobServiceClient.from_connection_string(_CONN)
    else:
        from azure.identity import DefaultAzureCredential
        svc = BlobServiceClient(f"https://{_ACCOUNT}.blob.core.windows.net",
                                credential=DefaultAzureCredential())
    return svc.get_container_client(_CONTAINER)


def list_samples() -> list[str]:
    """Sample blob names, sorted. Returns [] if unconfigured or unreachable (never raises)."""
    if not is_configured():
        return []
    try:
        return sorted(b.name for b in _container_client().list_blobs())
    except Exception:
        return []


def download_sample(name: str) -> bytes:
    """Download one sample by blob name. Raises on failure (the caller shows the error)."""
    return _container_client().download_blob(name).readall()
