# REBOOT (MCP Server)

REBOOT is **adaptive retrieval middleware** for code-focused LLM agents. It runs as a **Python FastAPI service** and exposes tools over **MCP (Model Context Protocol)** so any MCP-compatible agent can use REBOOT for codebase retrieval.

See [`NOTICE.md`](NOTICE.md) for usage/rights.

## What you get

- **MCP tools** (mounted at `http://localhost:<port>/mcp`):
  - `reboot_search(query, file_context?)`
  - `reboot_feedback(query_id, signal, node_ids?, details?)` (signal: `positive` | `negative`)
  - `reboot_explain(query_id?)`
  - `reboot_ingest(repo_path, incremental?, verbose?)` (async job)
  - `reboot_ingest_status(job_id)`
  - `reboot_ingest_cancel(job_id)`
- **REST endpoints** (same underlying logic as the MCP tools):
  - `GET /health`
  - `POST /query`
  - `POST /feedback`
  - `GET /reboot-explain`
  - `POST /ingest` (async job)
  - `GET /ingest-status/{job_id}`
  - `POST /ingest-cancel/{job_id}`
- **Graph visualizer UI** (served by the app; see `graphiti_visualizer` router in `middleware/main.py`).
- **Eval harness** under [`eval/`](eval/README.md) for repeatable retrieval evaluation runs.

## Quickstart (local dev)

From the repo root (the directory containing `docker-compose.yml`):

1) Start Neo4j:

```bash
docker compose up neo4j -d
```

2) Create `.env`:

```bash
cp .env.example .env
```

Fill in at least `OPENAI_API_KEY` (and `EMBEDDING_API_KEY` if you use a separate key/provider).

3) Create and activate a virtualenv, then install deps:

```bash
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r middleware/requirements.txt
```

4) Run the server:

```bash
python -m uvicorn middleware.main:app --reload --port 8000
```

5) Health check:

```bash
curl http://localhost:8000/health
```

## Configuration

REBOOT uses environment variables (loaded from `.env` in the repo root). See [`.env.example`](.env.example).

Common variables:

- **Neo4j**
  - `NEO4J_URI` (default `bolt://localhost:7687`)
  - `NEO4J_USER` (default `neo4j`)
  - `NEO4J_PASSWORD` (default `reboot_dev`)
- **LLM / embeddings**
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`
  - `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`
- **Service**
  - `SERVER_PORT` (default `8000`)
  - `SQLITE_PATH` (defaults to `middleware/feedback.db`)
  - `CONFIDENCE_DECAY_LAMBDA`, `DEMO_TIME_OFFSET_DAYS`

## Using from an MCP client

Point your MCP-capable agent at:

- `http://localhost:8000/mcp`

Then instruct it to use REBOOT for codebase questions. Example prompt snippet:

```text
- ALWAYS call reboot_search before answering questions about the codebase.
- Use reboot_explain if the user asks why context was retrieved.
- Call reboot_feedback with signal=positive/negative after the user reacts to the answer.
```

## Repo map

- [`ONBOARDING.md`](ONBOARDING.md): full local setup and run guide
- [`PROJECT.md`](PROJECT.md): architecture, tool/API details, design notes
- [`PLAN.md`](PLAN.md): implementation structure and original plan
- [`eval/README.md`](eval/README.md): eval harness

## Current limitations (intentional / known)

- **Feedback signals** are currently only `positive` and `negative`.
- **Async ingest** returns a `job_id`; clients should poll status (`/ingest-status/{job_id}` / `reboot_ingest_status`) rather than expecting an immediate `episodes_added` count.
- **Incremental ingest** uses a local state file (`middleware/ingestion/ingest_state.json`) to track per-repo file mtimes.
