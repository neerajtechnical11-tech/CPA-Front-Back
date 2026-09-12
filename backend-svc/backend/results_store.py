"""Long-term results store (Azure Cosmos DB).

Persists each assessment result as a JSON document so past runs can be listed and
re-downloaded later. Degrades gracefully: if Cosmos is not configured (or the SDK
is missing) every function is a safe no-op, so the app runs the same before the
Cosmos DB exists.

Config (config/settings.env):
  COSMOS_ENDPOINT   e.g. https://<account>.documents.azure.com:443/
  COSMOS_KEY        account key (blank -> Microsoft Entra ID via DefaultAzureCredential)
  COSMOS_DATABASE   default: compliance
  COSMOS_CONTAINER  default: assessments   (partitioned by /entityId)

Auth mirrors the other clients: key if COSMOS_KEY is set, otherwise Entra ID.
"""
import datetime as dt
import os
import uuid

_ENDPOINT = os.getenv("COSMOS_ENDPOINT", "").strip()
_KEY = os.getenv("COSMOS_KEY", "").strip()
_DB = os.getenv("COSMOS_DATABASE", "compliance")
_CONTAINER_NAME = os.getenv("COSMOS_CONTAINER", "assessments")

_container = None


def is_configured() -> bool:
    return bool(_ENDPOINT)


def _get_container():
    """Return the Cosmos container client, creating the DB/container on first use."""
    global _container
    if _container is not None:
        return _container
    from azure.cosmos import CosmosClient, PartitionKey
    if _KEY:
        client = CosmosClient(_ENDPOINT, credential=_KEY)
    else:
        from azure.identity import DefaultAzureCredential
        client = CosmosClient(_ENDPOINT, credential=DefaultAzureCredential())
    db = client.create_database_if_not_exists(id=_DB)
    _container = db.create_container_if_not_exists(
        id=_CONTAINER_NAME, partition_key=PartitionKey(path="/entityId"))
    return _container


def save(result: dict, user: str | None = None, entity: str | None = None,
         doc_name: str | None = None) -> str | None:
    """Persist one assessment. Returns the document id, or None if not stored."""
    if not is_configured():
        return None
    try:
        item_id = result.get("correlation_id") or uuid.uuid4().hex[:12]
        doc = {
            "id": item_id,
            "entityId": (entity or "entity"),
            "docName": doc_name or "",
            "user": user or "",
            "timestamp": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "complianceScore": result.get("compliance_score"),
            "summary": result.get("summary"),
            "result": result,   # full result so the report can be rebuilt exactly
        }
        _get_container().upsert_item(doc)
        return item_id
    except Exception:
        return None   # never break an assessment because storage failed


def list_recent(limit: int = 50, entity: str | None = None) -> list[dict]:
    """List past assessments (metadata only), newest first."""
    if not is_configured():
        return []
    try:
        q = ("SELECT c.id, c.entityId, c.docName, c.user, c.timestamp, "
             "c.complianceScore, c.summary FROM c")
        params = []
        if entity:
            q += " WHERE c.entityId = @e"
            params = [{"name": "@e", "value": entity}]
        q += " ORDER BY c.timestamp DESC"
        items = list(_get_container().query_items(
            query=q, parameters=params, enable_cross_partition_query=True))
        return items[:limit]
    except Exception:
        return []


def get(item_id: str) -> dict | None:
    """Fetch one full assessment document by id."""
    if not is_configured():
        return None
    try:
        items = list(_get_container().query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": item_id}],
            enable_cross_partition_query=True))
        return items[0] if items else None
    except Exception:
        return None
