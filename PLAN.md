# REBOOT MCP — Minimum Working Prototype Implementation Plan

## Directory Structure

```
reboot-mcp/
├── PROJECT.md
├── docker-compose.yml
├── .gitignore
├── .env.example
├── middleware/
│   ├── requirements.txt
│   ├── main.py                    # FastAPI app + MCP mount + REST endpoints
│   ├── config.py                  # Settings from env vars
│   ├── models.py                  # Pydantic request/response schemas
│   ├── mcp_tools.py               # FastMCP server + 4 tool definitions
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── client.py              # Graphiti client singleton + helpers
│   │   └── schemas.py             # Custom Graphiti entity types (CodeFunction, CodeClass, CodeModule)
│   ├── components/
│   │   ├── __init__.py
│   │   ├── query_classifier.py    # LLM-based query classification
│   │   ├── search_config.py       # Weight recipe selector
│   │   ├── confidence_ranker.py   # Post-ranking with confidence multipliers
│   │   └── feedback_logger.py     # SQLite feedback persistence
│   └── ingestion/
│       ├── __init__.py
│       └── parser.py              # tree-sitter code parsing + Graphiti ingestion
```

## Files to Create (in order)

### 1. `docker-compose.yml`
- Neo4j service (neo4j:5.26-community) with ports 7474 (browser) and 7687 (bolt)
- Default credentials: neo4j/reboot_dev
- Volume for data persistence

### 2. `.gitignore`
- Python, venv, __pycache__, .env, *.db, neo4j data

### 3. `.env.example`
- NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
- OPENAI_API_KEY (required by Graphiti for embeddings)
- OPENAI_BASE_URL (base URL for any OpenAI-compatible API)

### 4. `middleware/requirements.txt`
- fastapi, uvicorn[standard]
- fastmcp
- graphiti-core
- tree-sitter, tree-sitter-python, tree-sitter-javascript, tree-sitter-languages
- openai (for QueryClassifier + Graphiti embeddings)
- python-dotenv
- aiosqlite

### 5. `middleware/config.py`
- Load .env with dotenv
- Pydantic Settings class: neo4j creds, OpenAI key, SQLite path, server port

### 6. `middleware/models.py`
Pydantic models for:
- `QueryType` enum: conceptual, procedural, factual
- `SearchRequest` / `SearchResponse` / `SearchResultItem`
- `FeedbackSignal` enum + `FeedbackRequest`
- `ExplainResponse`
- `IngestRequest`
- Internal `QueryRecord` for tracking recent queries

### 7. `middleware/graph/schemas.py`
Custom Graphiti entity types (Pydantic BaseModel):
- `CodeFunction`: language, file_path, start_line, end_line, signature, docstring, confidence
- `CodeClass`: language, file_path, start_line, end_line, methods list, docstring, confidence
- `CodeModule`: language, file_path, imports list, confidence

### 8. `middleware/graph/client.py`
- `get_graphiti_client()` — async singleton returning initialized Graphiti instance
- `search_graph(query, config_weights)` — search Graphiti with given weight config
- `add_code_episode(name, body, entity_types)` — add parsed code as episode
- `update_node_confidence(node_uuid, delta)` — update confidence on a node

### 9. `middleware/components/query_classifier.py`
- `QueryClassifier` class
- `classify(query: str) -> QueryType` — calls OpenAI-compatible API with a classification prompt
- System prompt asks LLM to respond with exactly one of: conceptual, procedural, factual
- Falls back to "factual" on error

### 10. `middleware/components/search_config.py`
- `SearchConfig` dataclass: semantic_weight, recency_weight, structural_weight
- `SearchConfigSelector` class with hardcoded recipes:
  - conceptual → high semantic, low recency, medium structural
  - procedural → medium semantic, medium recency, high structural
  - factual → medium semantic, high recency, low structural
- `select(query_type: QueryType) -> SearchConfig`

### 11. `middleware/components/confidence_ranker.py`
- `ConfidencePostRanker` class
- `rerank(results, feedback_logger)` — multiplies each result's score by its node confidence
- Confidence lookup from SQLite via feedback_logger
- Default confidence = 1.0 for unseen nodes

### 12. `middleware/components/feedback_logger.py`
- `FeedbackLogger` class
- `init_db()` — create SQLite tables (feedback_events, node_confidence)
- `log_feedback(query_id, signal, node_ids, details)` — insert event row
- `get_confidence(node_id) -> float` — return current confidence score
- `update_confidence(node_id, signal)` — reinforce (+) or decay (-) confidence
  - positive: confidence = min(confidence * 1.1, 2.0)
  - negative: confidence = max(confidence * 0.9, 0.1)
- `get_feedback_history(query_id)` — return feedback events for a query

### 13. `middleware/ingestion/parser.py`
- `CodeParser` class using tree-sitter
- `parse_file(file_path) -> list[CodeNode]` — parse a single file, extract functions/classes
- `parse_repository(repo_path, incremental=False)` — walk repo, parse all supported files
- `ingest_to_graph(repo_path, graphiti_client, incremental)` — parse + add episodes to Graphiti
- Support Python and JavaScript initially (tree-sitter-python, tree-sitter-javascript)

### 14. `middleware/mcp_tools.py`
- Create `FastMCP("reboot")` instance
- Define 4 tools with `@mcp.tool` decorator:
  - `reboot_search(query, file_context=None)` — full pipeline: classify → config → search → rerank → return
  - `reboot_feedback(query_id, signal, details=None)` — log feedback + update confidence
  - `reboot_explain(query_id=None)` — return last query's classification, weights, scores
  - `reboot_ingest(repo_path, incremental=False)` — trigger ingestion pipeline

### 15. `middleware/main.py`
- FastAPI app with lifespan (init Graphiti + SQLite on startup, close on shutdown)
- Mount MCP server at `/mcp` via `mcp.http_app()`
- REST endpoints that call into the same logic:
  - `POST /query` → reboot_search logic
  - `POST /feedback` → reboot_feedback logic
  - `GET /reboot-explain` → reboot_explain logic
  - `POST /ingest` → reboot_ingest logic
  - `GET /health` → health check
- Store recent query records in-memory dict for explain lookups

## Key Design Decisions

1. **FastMCP mounted into FastAPI** — both MCP tools and REST endpoints share the same underlying component instances
2. **Shared state via app.state** — Graphiti client, FeedbackLogger, QueryClassifier, etc. stored on FastAPI app state and passed to MCP tools
3. **SQLite for feedback + confidence** — simple, no extra infra. aiosqlite for async access
4. **In-memory query log** — recent queries stored in a dict (keyed by query_id) for explain functionality. Bounded to last 1000 queries
5. **OpenAI-compatible LLM** — both Graphiti and QueryClassifier use the OpenAI client. User provides OPENAI_API_KEY and optionally OPENAI_BASE_URL to point at any compatible endpoint

## Implementation Order

1. Scaffolding: docker-compose.yml, .gitignore, .env.example
2. middleware/requirements.txt
3. middleware/config.py
4. middleware/models.py
5. middleware/graph/schemas.py
6. middleware/components/feedback_logger.py (standalone, no graph dependency)
7. middleware/graph/client.py
8. middleware/components/query_classifier.py
9. middleware/components/search_config.py
10. middleware/components/confidence_ranker.py
11. middleware/ingestion/parser.py
12. middleware/mcp_tools.py
13. middleware/main.py
