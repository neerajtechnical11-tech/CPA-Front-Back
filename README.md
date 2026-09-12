# Compliance Gap Analyzer - 2-container split (local scaffold)

A **2-container** version of the app, split at the natural boundary:

- **`frontend-svc/`** - Streamlit UI only. Calls the backend over HTTP. No secrets, no Azure SDKs.
- **`backend-svc/`** - FastAPI (`api.py`) wrapping **all** the existing logic: assessment
  pipeline, agentic routing, chat, evals, auth, notifications. Owns the data + secrets.

Your original `C:\compliance` is untouched - this is a separate copy in `C:\compliance-2c`.

## How they connect
```
[ frontend (Streamlit :8501) ]  --HTTP( BACKEND_API_URL )-->  [ backend (FastAPI :8000) ]
   streamlit_app.py, ui.py, entities.py                          api.py + backend/ + ingestion/
```
- The frontend finds the backend by **service name** (`http://backend:8000`) - works the same in
  docker-compose (compose DNS) and in AKS (Service DNS). Only the env-var value would change.
- **Inside** the backend container, `pipeline` still imports `scoring`, `azure_openai`, `routing_agent`,
  etc. as normal in-process calls. Only the frontend -> backend hop is HTTP.

## What's inside each container
| Container | Files | Talks to neighbours by |
|-----------|-------|------------------------|
| frontend  | `streamlit_app.py`, `ui.py`, `entities.py` | HTTP (`requests`) to the backend |
| backend   | `api.py`, `backend/*` (pipeline, scoring, azure clients, agent, routing_agent, chat, jira_agent, notify, auth, audit, evals, ...), `ingestion/*` | in-process imports |

## Endpoints (backend `api.py`)
`GET /health` · `POST /login` · `POST /assess` · `POST /chat` · `POST /eval` ·
`POST /route/plan` · `POST /route/execute`  (agentic routing is human-gated: plan, then approve).

## Run it locally
Prereqs: Docker Desktop, and a real `backend-svc/config/settings.env` (copied from your
`C:\compliance\config\settings.env`) with valid Azure keys **and your current public IP whitelisted**
on the Azure OpenAI / AI Search resources.

```
cd C:\compliance-2c
docker compose up --build
```
Open **http://localhost:8501** -> sign in (**admin / admin** on first run) -> Assess.

## Notes / scope
- The frontend is a **focused port** of the original `app/main.py`: it covers the core analyst flow
  (login, assess, results, chat, eval, agentic routing). The admin tabs (users/audit/backup/
  notifications/privacy/observability) are **not** ported yet - add each as a backend endpoint + a
  `requests` call here, following the same pattern.
- `settings.env` is **mounted**, never baked into the image (mirrors a k8s Secret).
- `auth.db` is **ephemeral** in this compose demo (recreated on restart). For persistence use a
  volume/PVC (single replica) or move to Postgres/Azure SQL (multi-replica) - see the project notes.
- `AGENTIC_ROUTING=true` should be set in `settings.env` for the Route tab to use the agent loop.

## Mapping to AKS later
Each `*-svc/` folder builds one image (own Dockerfile) -> push to ACR -> one k8s Deployment+Service
each. `BACKEND_API_URL` becomes `http://backend:8000` via k8s Service DNS. Deploy into its own
namespace (e.g. `compliance-v2`) to run alongside the existing `default` app.
