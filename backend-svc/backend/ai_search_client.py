"""Azure AI Search: vector index create / upload / retrieve.

Auth precedence:
  1. If AZURE_SEARCH_KEY is set -> admin-key auth (works with only Contributor,
     which can read the service admin key via `az search admin-key show`).
  2. Otherwise                  -> DefaultAzureCredential (Entra ID); the service
     must have API access control set to role-based, and the identity needs
     Search Service Contributor + Search Index Data Contributor/Reader.
"""
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery

from backend import resilience

_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
_INDEX = os.getenv("AZURE_SEARCH_INDEX", "compliance-baselines")
_DIM = int(os.getenv("EMBED_DIM", "3072"))
_KEY = os.getenv("AZURE_SEARCH_KEY")


def _credential():
    if _KEY:
        return AzureKeyCredential(_KEY)
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential()


def _search_client() -> SearchClient:
    return SearchClient(endpoint=_ENDPOINT, index_name=_INDEX, credential=_credential())


def _index_client() -> SearchIndexClient:
    return SearchIndexClient(endpoint=_ENDPOINT, credential=_credential())


def create_index():
    """Create the vector index (HNSW, cosine) if it does not exist."""
    ic = _index_client()
    if _INDEX in [i.name for i in ic.list_indexes()]:
        return
    index = SearchIndex(
        name=_INDEX,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="framework", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="control_id", type=SearchFieldDataType.String, filterable=True),
            SearchField(name="title", type=SearchFieldDataType.String, searchable=True),
            SearchField(name="text", type=SearchFieldDataType.String, searchable=True),
            SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="criticality", type=SearchFieldDataType.String, filterable=True),
            # Cross-framework crosswalk references (retrievable, not searched)
            SimpleField(name="nist_csf", type=SearchFieldDataType.String),
            SimpleField(name="iso_27002", type=SearchFieldDataType.String),
            SimpleField(name="nca_ecc", type=SearchFieldDataType.String),
            SimpleField(name="nist_800_53", type=SearchFieldDataType.String),
            SimpleField(name="cis", type=SearchFieldDataType.String),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=_DIM,
                vector_search_profile_name="hnsw-cosine",
                hidden=False,  # retrievable so we can compute full-coverage scoring
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw", parameters=HnswParameters(metric="cosine"))],
            profiles=[VectorSearchProfile(name="hnsw-cosine", algorithm_configuration_name="hnsw")],
        ),
    )
    ic.create_index(index)


def delete_index():
    """Drop the index (used to reset the baseline before a fresh load)."""
    ic = _index_client()
    if _INDEX in [i.name for i in ic.list_indexes()]:
        ic.delete_index(_INDEX)


def document_count() -> int:
    """Return the number of baseline documents in the index (health/observability)."""
    return _search_client().get_document_count()


def upload_documents(docs: list[dict]):
    """Upsert control records (each must include 'id' and 'embedding')."""
    _search_client().upload_documents(documents=docs)


def vector_search(vector: list[float], k: int = 3, framework: str | None = None) -> list[dict]:
    """Return the k baseline controls closest to `vector` (uses @search.score)."""
    vq = VectorizedQuery(vector=vector, k_nearest_neighbors=k, fields="embedding")

    def _q():
        results = _search_client().search(
            search_text=None,
            vector_queries=[vq],
            filter=f"framework eq '{framework}'" if framework else None,
            select=["framework", "control_id", "title", "text", "category", "criticality"],
            top=k,
        )
        return [{"score": r["@search.score"], **{f: r[f] for f in
                 ("framework", "control_id", "title", "text", "category", "criticality")}}
                for r in results]
    return resilience.call("ai-search", _q)


def get_all_controls(framework: str | None = None) -> list[dict]:
    """Fetch every control incl. embeddings (used for full-coverage scoring)."""
    def _q():
        results = _search_client().search(
            search_text="*",
            filter=f"framework eq '{framework}'" if framework else None,
            select=["framework", "control_id", "title", "text", "category", "criticality",
                    "nist_csf", "iso_27002", "nca_ecc", "nist_800_53", "cis", "embedding"],
            top=10000,
        )
        return list(results)  # materialize inside the guard so transient errors are caught
    return resilience.call("ai-search", _q)
