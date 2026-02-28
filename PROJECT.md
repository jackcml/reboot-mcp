# REBOOT — Retrieval Enhancement Based On Observed Trends

## What This Project Is

REBOOT is adaptive retrieval middleware for code-focused LLM agents. It exposes its capabilities as an **MCP (Model Context Protocol) server**, allowing any compatible AI coding assistant — OpenCode, Claude Code, Cursor, etc. — to leverage adaptive retrieval without requiring a custom fork. The system learns from developer usage patterns to continuously improve the context retrieved for LLM queries, without retraining the model or requiring manual labels.

## Problem

Standard RAG pipelines are static: deployed once and never updated. As codebases evolve, retrieved context becomes stale, degrading LLM output quality. Usage signals (reformulated queries, implicit corrections, explicit feedback) are discarded. REBOOT closes this feedback loop.

## Architecture Overview

```
Any MCP-Compatible Agent
  (OpenCode, Claude Code, Cursor, etc.)
       │
       │  MCP tool calls
       ▼
  REBOOT MCP Server / FastAPI Service (Python)
       │
       ├─ reboot_search           → tool: classify query, retrieve, rank, return context
       ├─ reboot_feedback         → tool: record usage signal (thumbs up/down, reformulation)
       ├─ reboot_explain          → tool: explain last retrieval decision with scores
       │
       │  Internal components:
       ├─ QueryClassifier         → categorizes intent: conceptual / procedural / factual
       ├─ SearchConfigSelector    → picks per-type weight recipes
       ├─ ConfidencePostRanker   → applies node confidence multipliers to results
       └─ FeedbackLogger         → records usage signals to SQLite
       │
       │  search() / confidence decay+reinforcement
       ▼
  Graphiti + Neo4j (Docker)      ← Knowledge Graph
       ▲
       │  code nodes / temporal edges
  Ingestion Pipeline
       ├─ tree-sitter             → function/class boundary parsing
       └─ Git History             → temporal edges
```

### Why MCP Instead of Forking an Agent

The original plan called for forking OpenCode and hardcoding HTTP hook points at query-send and response-receive. MCP replaces this with a cleaner approach:

| Concern | Fork Approach | MCP Approach |
|---|---|---|
| Maintenance | Must rebase on every upstream OpenCode update | Zero fork maintenance — REBOOT is a standalone server |
| Agent compatibility | Locked to one agent | Works with any MCP-compatible agent (OpenCode, Claude Code, Cursor, etc.) |
| Integration effort | Modify agent internals, understand Go/TS codebase | Point agent at MCP server URL + add tool descriptions to system prompt |
| Hook reliability | Guaranteed — hooks are hardcoded | Agent-driven — mitigated by clear tool descriptions and system prompt instructions |
| Portability | Sponsor is coupled to a specific agent | Sponsor can swap agents freely; REBOOT is agent-agnostic |

The one trade-off is that MCP tools are *agent-invoked* rather than *hardcoded*, meaning the agent could theoretically skip calling `reboot_search`. In practice, this is reliably mitigated by including clear instructions in the agent's system prompt (e.g., "Always use the reboot_search tool for codebase queries before answering"). Most MCP-compatible agents follow tool-use instructions consistently.

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Integration | MCP server (tool exposure via Model Context Protocol) |
| Middleware | Python + FastAPI |
| Knowledge Graph | Graphiti + Neo4j (Docker) |
| Code Parsing | tree-sitter (function/class boundaries) |
| Feedback Persistence | SQLite (event log) |
| Version Control | GitHub — feature branch workflow |

## MCP Tools Exposed

These are the tools the REBOOT MCP server exposes to connected agents:

### `reboot_search`
**Purpose:** Retrieve adaptive, confidence-ranked context for a developer query.

**Input:**
- `query` (string) — the developer's natural language question
- `file_context` (string, optional) — current file path or surrounding code for locality hints

**Behavior:** Classifies the query type (conceptual/procedural/factual), selects the appropriate SearchConfig weight recipe, queries Graphiti, applies confidence post-ranking, and returns ranked code snippets with metadata.

**Output:** List of ranked context items, each with source file, snippet, confidence score, and relevance explanation.

### `reboot_feedback`
**Purpose:** Record a usage signal to drive confidence reinforcement or decay.

**Input:**
- `query_id` (string) — ID of the query this feedback relates to
- `signal` (enum) — one of: `positive`, `negative`, `reformulation`, `context_used`, `context_ignored`
- `details` (string, optional) — free-text elaboration

**Behavior:** Logs the signal to SQLite and triggers async confidence updates on affected knowledge graph nodes.

### `reboot_explain`
**Purpose:** Introspect the last retrieval decision for debugging and transparency.

**Input:**
- `query_id` (string, optional) — defaults to most recent query

**Output:** Breakdown of classification result, SearchConfig weights applied, raw vs. post-ranked scores, confidence multipliers per node, and which feedback signals have historically affected those nodes.

### `reboot_ingest`
**Purpose:** Trigger codebase ingestion into the knowledge graph.

**Input:**
- `repo_path` (string) — path to the repository root
- `incremental` (bool, optional) — if true, only process changed files since last ingestion

**Behavior:** Runs tree-sitter parsing to extract function/class boundaries, processes Git history for temporal edges, and indexes everything into Graphiti/Neo4j.

## Internal Components

### QueryClassifier
Categorizes incoming queries as **conceptual**, **procedural**, or **factual** using an LLM call. This classification drives which `SearchConfig` weight recipe is applied.

### SearchConfigSelector
Maps query types to weight configurations that control how Graphiti search results are ranked. Different query types benefit from different mixes of semantic similarity, recency, and structural proximity.

### ConfidencePostRanker
Applies per-node confidence multipliers to search results. Confidence scores are reinforced by positive usage signals and decay exponentially over time when nodes go unused or receive negative feedback.

### FeedbackLogger
Records behavioral signals to SQLite: query reformulations, explicit thumbs-up/down, whether retrieved context was actually used. These signals drive the confidence decay/reinforcement cycle.

### Ingestion Pipeline
Uses tree-sitter to parse a target codebase into function/class-level nodes and Git history to create temporal edges. These are indexed into Graphiti/Neo4j.

## Design Decisions

- **Knowledge Graph over Vector DB:** Graphiti (Neo4j) was chosen for bi-temporal edges and relationship traversal. Vector DBs like Pinecone/Chroma lack the temporal awareness needed for confidence decay logic.
- **MCP server over agent fork:** Exposing tools via MCP decouples REBOOT from any specific agent, eliminates fork maintenance, and makes the system portable across the growing ecosystem of MCP-compatible tools.
- **LLM-based query classification:** Enables per-type retrieval strategies rather than one-size-fits-all.
- **Synthetic evaluation dataset:** A scripted query+feedback dataset against a real open-source codebase enables measurable validation before live deployment.

## Project Phases

| Phase | Dates | Focus |
|---|---|---|
| 0 | 1/26–2/23 | Setup & Research — Neo4j/Docker env, Graphiti research, node schema design, stub pipeline |
| 1a | 2/16–3/9 | Core Build — Middleware: 4 REBOOT components, FastAPI service, MCP server setup |
| 1b | 2/16–3/9 | Core Build — Ingestion: tree-sitter ingestion, index target repo in Graphiti/Neo4j |
| 2 | 3/9–3/23 | Feedback Loop — Confidence decay/reinforcement, synthetic dataset, measurable adaptation |
| 3 | 3/23–4/13 | Tuning & Stretch — SearchConfig weight tuning, Precision@K / MRR eval, reboot_explain tool |
| 4 | 4/6–4/22 | Demo & Docs — Sponsor demo, Honda blog post, final documentation |

## Conventions & Patterns

- **MCP server:** Implemented alongside the FastAPI service. Tools are defined with clear input schemas and descriptions so agents know when and how to invoke them.
- **API service:** FastAPI with async endpoints. Pydantic models for request/response schemas.
- **Graph nodes:** Custom Pydantic entity schemas for Graphiti nodes (code functions, classes, modules).
- **Confidence scores:** Stored as node metadata in Neo4j. Reinforced on positive signals, exponential decay over time.
- **System prompt guidance:** A reference system prompt snippet is provided (see below) for configuring agents to use REBOOT tools reliably.
- **Testing:** pytest for integration tests. Synthetic dataset for retrieval quality evaluation.
- **Metrics:** Precision@K and MRR (Mean Reciprocal Rank) for measuring retrieval improvement.

## Running Locally

```bash
# Start Neo4j
docker compose up neo4j -d

# Start REBOOT middleware + MCP server
cd middleware
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Connect an agent to the MCP server
# Example: Claude Code
claude mcp add reboot http://localhost:8000/mcp

# Example: OpenCode (no fork needed)
# Add to opencode config:
#   mcp_servers:
#     - url: http://localhost:8000/mcp
#       name: reboot
```

## Reference System Prompt Snippet

Include this (or similar) in the agent's system prompt to ensure reliable tool usage:

```
You have access to REBOOT, an adaptive retrieval system for this codebase.

- ALWAYS call `reboot_search` before answering questions about the codebase.
  Pass the developer's query and, if available, the current file path as context.
- After the developer accepts or rejects your answer, call `reboot_feedback`
  with the appropriate signal so REBOOT can learn from the interaction.
- If the developer asks why certain context was retrieved, call `reboot_explain`.
```

## Key Endpoints (REST, for direct integration if needed)

| Method | Path | Description |
|---|---|---|
| POST | `/query` | Classify query, retrieve from Graphiti, rank with confidence, return context |
| POST | `/feedback` | Record usage signal (thumbs up/down, reformulation, context-used) |
| GET | `/reboot-explain` | Explain last retrieval decision: scores, sources, confidence values |
| GET | `/health` | Service health check |
| POST | `/ingest` | Trigger codebase ingestion into knowledge graph |

These REST endpoints remain available for non-MCP integrations or direct testing. The MCP tools call into the same underlying logic.
