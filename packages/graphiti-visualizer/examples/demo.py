"""
Standalone demo for graphiti-visualizer.

Serves a mock knowledge graph with no Neo4j or Graphiti required.
Nodes are added progressively to showcase the live-evolution feature.

Usage:
    pip install fastapi uvicorn
    python demo.py

Then open http://localhost:8000/visualizer
"""

import asyncio
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# Mock graph data
# ---------------------------------------------------------------------------

MOCK_LABELS = ["CodeFunction", "CodeClass", "CodeModule", "Episodic"]

SEED_NODES = [
    {"name": "main", "labels": ["Entity", "CodeFunction"], "summary": "Application entry point", "attrs": {"file_path": "src/main.py", "signature": "def main()", "language": "python", "start_line": 1, "end_line": 25}},
    {"name": "App", "labels": ["Entity", "CodeClass"], "summary": "Core application class that manages lifecycle and routing", "attrs": {"file_path": "src/app.py", "signature": "class App", "language": "python", "methods": ["run", "stop", "configure"], "start_line": 10, "end_line": 85}},
    {"name": "Router", "labels": ["Entity", "CodeClass"], "summary": "HTTP request router with middleware support", "attrs": {"file_path": "src/router.py", "signature": "class Router", "language": "python", "methods": ["add_route", "match", "dispatch"], "start_line": 5, "end_line": 120}},
    {"name": "Database", "labels": ["Entity", "CodeClass"], "summary": "Async database connection pool and query executor", "attrs": {"file_path": "src/db.py", "signature": "class Database", "language": "python", "methods": ["connect", "execute", "close"], "start_line": 1, "end_line": 95}},
    {"name": "handle_request", "labels": ["Entity", "CodeFunction"], "summary": "Top-level request handler that dispatches to route handlers", "attrs": {"file_path": "src/router.py", "signature": "async def handle_request(request)", "language": "python", "start_line": 122, "end_line": 145}},
    {"name": "authenticate", "labels": ["Entity", "CodeFunction"], "summary": "Validates JWT tokens and returns user context", "attrs": {"file_path": "src/auth.py", "signature": "async def authenticate(token)", "language": "python", "start_line": 30, "end_line": 58}},
    {"name": "UserModel", "labels": ["Entity", "CodeClass"], "summary": "ORM model for the users table with validation", "attrs": {"file_path": "src/models/user.py", "signature": "class UserModel", "language": "python", "methods": ["create", "find_by_id", "update", "delete"], "start_line": 8, "end_line": 72}},
    {"name": "src.config", "labels": ["Entity", "CodeModule"], "summary": "Configuration module loading from env vars and YAML", "attrs": {"file_path": "src/config.py", "language": "python", "imports": ["os", "yaml", "pydantic"]}},
    {"name": "src.utils", "labels": ["Entity", "CodeModule"], "summary": "Shared utility functions used across the codebase", "attrs": {"file_path": "src/utils.py", "language": "python", "imports": ["hashlib", "datetime", "typing"]}},
    {"name": "parse_query", "labels": ["Entity", "CodeFunction"], "summary": "Parses SQL-like query strings into AST nodes", "attrs": {"file_path": "src/query.py", "signature": "def parse_query(raw_sql)", "language": "python", "start_line": 15, "end_line": 48}},
    {"name": "Cache", "labels": ["Entity", "CodeClass"], "summary": "LRU cache with TTL support for query results", "attrs": {"file_path": "src/cache.py", "signature": "class Cache", "language": "python", "methods": ["get", "set", "invalidate", "clear"], "start_line": 3, "end_line": 67}},
    {"name": "Logger", "labels": ["Entity", "CodeClass"], "summary": "Structured JSON logger with context propagation", "attrs": {"file_path": "src/logger.py", "signature": "class Logger", "language": "python", "methods": ["info", "error", "debug", "with_context"], "start_line": 1, "end_line": 55}},
]

SEED_EDGES = [
    {"source": "main", "target": "App", "name": "creates", "rel_type": "RELATES_TO", "fact": "main() creates and runs an App instance"},
    {"source": "App", "target": "Router", "name": "uses", "rel_type": "RELATES_TO", "fact": "App delegates HTTP routing to Router"},
    {"source": "App", "target": "Database", "name": "connects", "rel_type": "RELATES_TO", "fact": "App initializes Database on startup"},
    {"source": "Router", "target": "handle_request", "name": "dispatches_to", "rel_type": "RELATES_TO", "fact": "Router dispatches matched routes to handle_request"},
    {"source": "handle_request", "target": "authenticate", "name": "calls", "rel_type": "RELATES_TO", "fact": "handle_request calls authenticate for protected routes"},
    {"source": "authenticate", "target": "UserModel", "name": "queries", "rel_type": "RELATES_TO", "fact": "authenticate queries UserModel to validate tokens"},
    {"source": "UserModel", "target": "Database", "name": "uses", "rel_type": "RELATES_TO", "fact": "UserModel executes queries through Database"},
    {"source": "App", "target": "src.config", "name": "imports", "rel_type": "RELATES_TO", "fact": "App reads configuration from src.config"},
    {"source": "App", "target": "Logger", "name": "uses", "rel_type": "RELATES_TO", "fact": "App initializes Logger for structured logging"},
    {"source": "Database", "target": "Logger", "name": "logs_via", "rel_type": "RELATES_TO", "fact": "Database logs query execution through Logger"},
    {"source": "parse_query", "target": "Cache", "name": "caches_in", "rel_type": "RELATES_TO", "fact": "parse_query caches parsed ASTs in Cache"},
    {"source": "handle_request", "target": "Logger", "name": "logs_via", "rel_type": "RELATES_TO", "fact": "handle_request logs incoming requests"},
    {"source": "Router", "target": "src.utils", "name": "imports", "rel_type": "RELATES_TO", "fact": "Router uses utility functions from src.utils"},
]

EXTRA_NODES = [
    {"name": "Middleware", "labels": ["Entity", "CodeClass"], "summary": "Pluggable middleware chain for request/response processing", "attrs": {"file_path": "src/middleware.py", "signature": "class Middleware", "language": "python", "methods": ["before", "after", "register"]}},
    {"name": "validate_input", "labels": ["Entity", "CodeFunction"], "summary": "Validates request body against JSON schema", "attrs": {"file_path": "src/validation.py", "signature": "def validate_input(schema, data)", "language": "python"}},
    {"name": "SessionStore", "labels": ["Entity", "CodeClass"], "summary": "Server-side session storage backed by Redis", "attrs": {"file_path": "src/session.py", "signature": "class SessionStore", "language": "python", "methods": ["get", "set", "destroy"]}},
    {"name": "rate_limit", "labels": ["Entity", "CodeFunction"], "summary": "Token bucket rate limiter per client IP", "attrs": {"file_path": "src/ratelimit.py", "signature": "async def rate_limit(request)", "language": "python"}},
    {"name": "Serializer", "labels": ["Entity", "CodeClass"], "summary": "JSON/MessagePack response serializer", "attrs": {"file_path": "src/serializer.py", "signature": "class Serializer", "language": "python", "methods": ["to_json", "to_msgpack", "from_bytes"]}},
    {"name": "HealthCheck", "labels": ["Entity", "CodeFunction"], "summary": "Liveness and readiness probe endpoint", "attrs": {"file_path": "src/health.py", "signature": "async def health_check()", "language": "python"}},
    {"name": "migration_001", "labels": ["Episodic"], "summary": "Initial schema migration creating users and sessions tables", "attrs": {"file_path": "migrations/001_init.sql"}},
    {"name": "migration_002", "labels": ["Episodic"], "summary": "Add indexes on users.email and sessions.token", "attrs": {"file_path": "migrations/002_indexes.sql"}},
]

EXTRA_EDGES = [
    {"source": "Router", "target": "Middleware", "name": "chains", "rel_type": "RELATES_TO", "fact": "Router runs Middleware chain before dispatch"},
    {"source": "Middleware", "target": "rate_limit", "name": "includes", "rel_type": "RELATES_TO", "fact": "Middleware includes rate_limit as a step"},
    {"source": "Middleware", "target": "authenticate", "name": "includes", "rel_type": "RELATES_TO", "fact": "Middleware includes authenticate as a step"},
    {"source": "handle_request", "target": "validate_input", "name": "calls", "rel_type": "RELATES_TO", "fact": "handle_request validates input before processing"},
    {"source": "authenticate", "target": "SessionStore", "name": "queries", "rel_type": "RELATES_TO", "fact": "authenticate checks SessionStore for active sessions"},
    {"source": "handle_request", "target": "Serializer", "name": "uses", "rel_type": "RELATES_TO", "fact": "handle_request serializes responses via Serializer"},
    {"source": "App", "target": "HealthCheck", "name": "registers", "rel_type": "RELATES_TO", "fact": "App registers HealthCheck at /health endpoint"},
    {"source": "Database", "target": "migration_001", "name": "applied", "rel_type": "MENTIONS", "fact": "Database schema created by migration_001"},
]


class MockGraph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self._name_to_uuid: dict[str, str] = {}
        self._extra_index = 0
        self._extra_edge_index = 0

    def _add_node(self, spec: dict) -> str:
        uid = str(uuid.uuid4())
        self._name_to_uuid[spec["name"]] = uid
        self.nodes[uid] = {
            "uuid": uid,
            "name": spec["name"],
            "labels": spec["labels"],
            "summary": spec.get("summary", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attrs": spec.get("attrs", {}),
        }
        return uid

    def _add_edge(self, spec: dict) -> str | None:
        src = self._name_to_uuid.get(spec["source"])
        tgt = self._name_to_uuid.get(spec["target"])
        if not src or not tgt:
            return None
        uid = str(uuid.uuid4())
        self.edges[uid] = {
            "uuid": uid,
            "source": src,
            "target": tgt,
            "name": spec["name"],
            "rel_type": spec.get("rel_type", "RELATES_TO"),
            "fact": spec.get("fact", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return uid

    def seed(self):
        for spec in SEED_NODES:
            self._add_node(spec)
        for spec in SEED_EDGES:
            self._add_edge(spec)

    def grow(self) -> bool:
        """Add one extra node (and its edges) to simulate evolution. Returns False when exhausted."""
        if self._extra_index >= len(EXTRA_NODES):
            return False
        spec = EXTRA_NODES[self._extra_index]
        self._add_node(spec)
        self._extra_index += 1

        # Add any edges whose endpoints now exist.
        while self._extra_edge_index < len(EXTRA_EDGES):
            e = EXTRA_EDGES[self._extra_edge_index]
            src = self._name_to_uuid.get(e["source"])
            tgt = self._name_to_uuid.get(e["target"])
            if src and tgt:
                self._add_edge(e)
                self._extra_edge_index += 1
            else:
                break
        return True

    def get_labels(self) -> list[str]:
        labels: set[str] = set()
        for n in self.nodes.values():
            labels.update(n["labels"])
        return sorted(labels)


graph = MockGraph()
graph.seed()

# ---------------------------------------------------------------------------
# Color palette (mirrors the package)
# ---------------------------------------------------------------------------

COLOR_PALETTE = [
    "#4A90D9", "#5CB85C", "#F0AD4E", "#D9534F", "#5BC0DE", "#8E44AD",
    "#1ABC9C", "#E67E22", "#2ECC71", "#3498DB", "#E74C3C", "#9B59B6",
    "#F39C12", "#16A085", "#C0392B", "#2980B9",
]
DEFAULT_UNCHECKED = {"Episodic", "Community", "Saga"}


def _label_color(label: str) -> str:
    return COLOR_PALETTE[hash(label) % len(COLOR_PALETTE)]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Graphiti Visualizer Demo")

_HTML_PATH = Path(__file__).resolve().parent.parent / "graphiti_visualizer" / "static" / "visualizer.html"
_HTML = _HTML_PATH.read_text(encoding="utf-8")


@app.get("/visualizer", response_class=HTMLResponse)
async def visualizer_page():
    return _HTML


@app.get("/api/graph")
async def get_graph_data(
    limit: int = 500,
    labels: str | None = Query(default=None),
):
    label_filter = None
    if labels:
        label_filter = set(l.strip() for l in labels.split(",") if l.strip())

    nodes = []
    node_uuids: set[str] = set()
    for uid, n in list(graph.nodes.items())[:limit]:
        if label_filter and not label_filter.intersection(n["labels"]):
            continue
        node_uuids.add(uid)
        specific = [l for l in n["labels"] if l != "Entity"]
        group = specific[0] if specific else "Entity"

        title_parts = [f"<b>{n['name']}</b>"]
        if n["summary"]:
            title_parts.append(f"<i>{n['summary'][:120]}</i>")
        fp = n["attrs"].get("file_path")
        if fp:
            title_parts.append(f"File: {fp}")

        nodes.append({
            "id": uid,
            "label": n["name"],
            "group": group,
            "title": "<br>".join(title_parts),
            "created_at": n["created_at"],
            "nodeLabels": n["labels"],
        })

    edges = []
    for uid, e in graph.edges.items():
        if e["source"] in node_uuids and e["target"] in node_uuids:
            edges.append({
                "id": uid,
                "from": e["source"],
                "to": e["target"],
                "label": e["name"],
                "title": e["fact"] or e["name"],
                "relType": e["rel_type"],
                "created_at": e["created_at"],
            })

    groups = {n["group"] for n in nodes}
    color_map = {g: _label_color(g) for g in groups}

    return {
        "nodes": nodes,
        "edges": edges,
        "colorMap": color_map,
        "defaultUnchecked": list(DEFAULT_UNCHECKED),
    }


@app.get("/api/graph/labels")
async def get_graph_labels():
    labels = graph.get_labels()
    color_map = {l: _label_color(l) for l in labels}
    return {
        "labels": labels,
        "colorMap": color_map,
        "defaultUnchecked": list(DEFAULT_UNCHECKED),
    }


@app.get("/api/graph/node/{uuid}")
async def get_node_detail(uuid: str):
    n = graph.nodes.get(uuid)
    if not n:
        return {"error": "Node not found"}

    attrs = {k: v for k, v in n["attrs"].items()}

    connections = []
    for e in graph.edges.values():
        if e["source"] == uuid:
            target = graph.nodes.get(e["target"])
            connections.append({
                "relType": e["rel_type"],
                "edgeName": e["name"],
                "fact": e["fact"],
                "connectedName": target["name"] if target else e["target"],
                "connectedUuid": e["target"],
                "direction": "outgoing",
            })
        elif e["target"] == uuid:
            source = graph.nodes.get(e["source"])
            connections.append({
                "relType": e["rel_type"],
                "edgeName": e["name"],
                "fact": e["fact"],
                "connectedName": source["name"] if source else e["source"],
                "connectedUuid": e["source"],
                "direction": "incoming",
            })

    return {
        "uuid": n["uuid"],
        "name": n["name"],
        "labels": n["labels"],
        "summary": n["summary"],
        "created_at": n["created_at"],
        "attributes": attrs,
        "connections": connections[:20],
    }


# ---------------------------------------------------------------------------
# Background task: grow the graph every 8 seconds
# ---------------------------------------------------------------------------

async def _grow_loop():
    await asyncio.sleep(5)  # let the server start
    while True:
        added = graph.grow()
        if not added:
            break
        await asyncio.sleep(8)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_grow_loop())


if __name__ == "__main__":
    uvicorn.run("demo:app", host="127.0.0.1", port=8000, reload=False)
