
## REBOOT – Local Development & Run Guide

This document walks you through **cloning the repo, installing prerequisites, configuring the environment, and running the REBOOT service locally** from scratch.

It assumes **no prior setup** beyond a working computer and internet connection.

For a shorter “front door” overview, start with [`README.md`](README.md).

---

## 1. What REBOOT Is (High Level)

REBOOT is **adaptive retrieval middleware** for code-focused LLM agents. It runs as a **Python FastAPI web service** and exposes tools over **MCP (Model Context Protocol)**.

At a high level, when you run REBOOT locally you are starting:

- **A Neo4j database** (via Docker) to store a code knowledge graph.
- **A FastAPI + MCP server** (via Uvicorn) that:
  - Ingests codebases into Neo4j.
  - Answers “search” queries against that graph.
  - Records feedback to improve ranking over time.

---

## 2. Prerequisites

Before you start, make sure you have:

- **Git**
  - Check: `git --version`
  - Install: [Git downloads](https://git-scm.com/downloads)

- **Python 3.10+**
  - Check: `python --version` (or `python3 --version` on some systems)
  - Install:
    - Windows/macOS: [Python downloads](https://www.python.org/downloads/)
    - Linux: via your distro’s package manager, e.g. `sudo apt install python3 python3-venv`

- **Docker (Docker Desktop or Docker Engine)**
  - We use Docker to run **Neo4j**.
  - Install: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  - After installation, make sure Docker is **running**.

- **OpenAI (or compatible) API key**
  - REBOOT uses an OpenAI-compatible API for:
    - Query classification.
    - Graphiti’s LLM/embedding operations.
  - You’ll need:
    - **API key** (e.g. `sk-...`)
    - Optionally a **base URL** if you’re not using api.openai.com.

---

## 3. Clone the Repository

Pick a directory where you keep code, then:

```bash
git clone https://github.com/<org-or-user>/reboot-mcp.git
cd reboot-mcp/reboot-mcp
```

> **Note:** The repo root you’ll work from is the inner `reboot-mcp` directory (the one containing `PROJECT.md`, `docker-compose.yml`, `middleware/`, etc.).

---

## 4. Configure Environment Variables (`.env`)

The app reads configuration from a `.env` file in the project root.

1. **Copy the example file**:

   ```bash
   cp .env.example .env
   # Windows PowerShell alternative:
   # Copy-Item .env.example .env
   ```

2. **Open `.env`** in your editor and fill in values:

   At minimum:

   ```env
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=reboot_dev

   OPENAI_API_KEY=sk-your-key-here
   OPENAI_BASE_URL=https://api.openai.com/v1
   OPENAI_MODEL=gpt-5-mini

   EMBEDDING_API_KEY=sk-your-embedding-key-here
   EMBEDDING_BASE_URL=https://api.openai.com/v1
   EMBEDDING_MODEL=text-embedding-3-small
   ```

   - **Neo4j settings**:
     - These should match the defaults in `docker-compose.yml`:
       - User: `neo4j`
       - Password: `reboot_dev`
   - **OpenAI / embeddings**:
     - `OPENAI_API_KEY` and `EMBEDDING_API_KEY` can be the same key if you are using OpenAI’s hosted API.
     - If you’re using a self-hosted or compatible provider, adjust the `*_BASE_URL` values accordingly.

You can always change these later; the service will reload settings when restarted.

---

## 5. Start Neo4j with Docker

From the **project root** (the directory containing `docker-compose.yml`):

```bash
docker compose up neo4j -d
```

- This:
  - Pulls the `neo4j:5.26-community` image (if needed).
  - Starts a container named something like `reboot-mcp-neo4j-1`.
  - Exposes:
    - **Neo4j Browser** at `http://localhost:7474`
    - **Bolt** at `bolt://localhost:7687`

Optional sanity check:

- Open a browser to `http://localhost:7474`
- Log in with:
  - Username: `neo4j`
  - Password: `reboot_dev`

If you see the Neo4j interface, the database is running.

---

## 6. Create a Python Virtual Environment

We strongly recommend using a **virtual environment** to isolate dependencies.

From the **project root**:

### 6.1 Create the venv

- **Windows (PowerShell)**:

  ```powershell
  python -m venv .venv
  ```

- **macOS / Linux**:

  ```bash
  python3 -m venv .venv
  ```

### 6.2 Activate the venv

- **Windows (PowerShell)**:

  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

- **macOS / Linux (bash/zsh)**:

  ```bash
  source .venv/bin/activate
  ```

You should now see `(.venv)` at the beginning of your shell prompt.

To **deactivate** later:

```bash
deactivate
```

---

## 7. Install Python Dependencies

With the virtual environment **activated** and still in the project root:

```bash
pip install -r middleware/requirements.txt
```

This installs:

- **FastAPI** and **Uvicorn** (web server).
- **fastmcp** (MCP server integration).
- **graphiti-core** (graph / Neo4j integration).
- **tree-sitter** language parsers.
- **OpenAI** client.
- Supporting libraries like `aiosqlite`, `pydantic-settings`, etc.

If the install succeeds, you’re ready to run the service.

---

## 8. Run the REBOOT Service Locally

From the **project root**, with the venv **still active**:

```bash
python -m uvicorn middleware.main:app --reload --port 8000
```

What this does:

- Starts Uvicorn on `http://127.0.0.1:8000` (also reachable as `http://localhost:8000`).
- Runs the FastAPI app defined in `middleware/main.py`.
- On startup, the app:
  - Connects to Neo4j with Graphiti.
  - Builds required indices and constraints.
  - Initializes the MCP tools and feedback logger.
  - Mounts the MCP server at `http://localhost:8000/mcp`.

You’ll see log lines like:

- `Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)`
- `Application startup complete.`

Some **Neo4j index warnings** like “EquivalentSchemaRuleAlreadyExists” may appear if indexes are already present; these are expected and can be safely ignored as long as startup completes.

---

## 9. Verify the Service Is Healthy

Open a **second terminal** (keep the one running Uvicorn open) and, from any directory:

### 9.1 Using curl (macOS/Linux or Windows with curl)

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

### 9.2 Using PowerShell (`Invoke-RestMethod`)

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected output:

```text
status
------
ok
```

If you see that, **REBOOT is successfully running locally**.

---

## 10. (Optional) Ingest a Codebase and Run a Query

To see REBOOT do something meaningful, you’ll typically:

1. **Ingest a repository** into Neo4j.
2. **Search** against that ingested code.

### 10.1 Ingest a repository

Pick a local repo (for example, another project on your machine) and note its absolute path, e.g.:

- Windows: `C:\Users\<you>\Projects\some-repo`
- macOS/Linux: `/Users/<you>/Projects/some-repo`

Then call the `/ingest` endpoint.

**PowerShell example:**

```powershell
$body = @{
  repo_path  = "C:\path\to\your\repo"
  incremental = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/ingest `
  -ContentType 'application/json' `
  -Body $body
```

**curl example:**

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/absolute/path/to/your/repo", "incremental": false}'
```

`/ingest` starts an **async ingest job** and returns a `job_id` immediately. You can poll status until it completes.

You should get a JSON response like:

```json
{
  "status": "started",
  "job_id": "..."
}
```

Poll status:

```bash
curl http://localhost:8000/ingest-status/<job_id>
```

Cancel (optional):

```bash
curl -X POST http://localhost:8000/ingest-cancel/<job_id>
```

### 10.2 Run a query

Now call `/query` with a natural-language question about the ingested repo:

**PowerShell:**

```powershell
$body = @{
  query        = "Where is the main entry point defined?"
  file_context = $null
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/query `
  -ContentType 'application/json' `
  -Body $body
```

**curl:**

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Where is the main entry point defined?", "file_context": null}'
```

You should see a response containing:

- A `query_id`.
- The inferred `query_type` (conceptual/procedural/factual).
- A list of `results` with node IDs, names, content snippets, and scores.

---

## 11. (Optional) MCP Client Integration

REBOOT also exposes an **MCP server** at:

- `http://localhost:8000/mcp`

Any MCP-compatible client (e.g. some LLM IDEs, Claude CLI, OpenCode, etc.) can be pointed at that URL.

In general, you would:

- Add a new **MCP server** in your client configuration with:
  - **Name**: something like `reboot`
  - **URL**: `http://localhost:8000/mcp`
- Use a system prompt or config that instructs the agent to:
  - **Always call `reboot_search`** before answering questions about the codebase.
  - **Call `reboot_feedback`** after users accept or reject answers.
  - **Call `reboot_explain`** when users ask why certain context was retrieved.

Exact steps differ per tool, so refer to the client’s MCP documentation.

---

## 12. Common Pitfalls & Tips

- **`uvicorn` command not found**
  - Use `python -m uvicorn ...` instead of relying on the `uvicorn` script being on `PATH`.
  - Ensure your virtual environment is activated.

- **Docker or Neo4j not running**
  - If the app hangs or logs connection errors to Neo4j, confirm:
    - Docker is running.
    - `docker compose up neo4j -d` is still active (container not exited).

- **OpenAI authentication errors**
  - Double-check:
    - `OPENAI_API_KEY` in `.env` is correct.
    - If using a custom base URL, that the host is reachable and supports the OpenAI-compatible API.

- **Changing configuration**
  - After editing `.env`, **restart the Uvicorn process** so changes are picked up.

---

## 13. Quick Start Summary

- **Clone**: `git clone ... && cd reboot-mcp/reboot-mcp`
- **Configure**: copy `.env.example` → `.env`, add your keys.
- **Start Neo4j**: `docker compose up neo4j -d`
- **Create venv**: `python -m venv .venv` and activate it.
- **Install deps**: `pip install -r middleware/requirements.txt`
- **Run server**: `python -m uvicorn middleware.main:app --reload --port 8000`
- **Health check**: `curl http://localhost:8000/health` or `Invoke-RestMethod http://localhost:8000/health`

Once you see `{"status":"ok"}`, you’re ready to ingest a repo and start experimenting with REBOOT.
```