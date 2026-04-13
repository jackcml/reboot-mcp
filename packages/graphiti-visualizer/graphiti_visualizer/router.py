from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

_STATIC_DIR = Path(__file__).parent / "static"
_VISUALIZER_HTML = (_STATIC_DIR / "visualizer.html").read_text(encoding="utf-8")

# 16-color palette for auto-assigning label colors.
COLOR_PALETTE = [
    "#4A90D9",
    "#5CB85C",
    "#F0AD4E",
    "#D9534F",
    "#5BC0DE",
    "#8E44AD",
    "#1ABC9C",
    "#E67E22",
    "#2ECC71",
    "#3498DB",
    "#E74C3C",
    "#9B59B6",
    "#F39C12",
    "#16A085",
    "#C0392B",
    "#2980B9",
]

# Labels that are unchecked by default in the filter UI.
DEFAULT_UNCHECKED = {"Episodic", "Community", "Saga"}


def _label_color(label: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and label in overrides:
        return overrides[label]
    return COLOR_PALETTE[hash(label) % len(COLOR_PALETTE)]


def _sanitize_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Strip large list properties (embeddings) and convert non-serializable values."""
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if isinstance(v, list) and len(v) > 50:
            continue
        if k.endswith("_embedding"):
            continue
        out[k] = _safe_value(v)
    return out


def _safe_value(v: Any) -> Any:
    """Convert neo4j types and other non-JSON-serializable values to strings."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return [_safe_value(i) for i in v]
    return str(v)


def create_visualizer_router(
    get_client: Callable[[], Awaitable[Any]],
    path_prefix: str = "",
    color_overrides: dict[str, str] | None = None,
    feedback_state_attr: str | None = None,
) -> APIRouter:
    """Create a FastAPI router that serves a Graphiti knowledge graph visualizer.

    Args:
        get_client: Async callable returning an initialized Graphiti instance.
        path_prefix: Optional prefix prepended to all routes.
        color_overrides: Optional dict mapping Neo4j label -> hex color.
        feedback_state_attr: If set, ``request.app.state.<name>`` is read for node
            detail enrichment. The object must provide ``async def get_confidence_detail(node_id)``.
    """
    router = APIRouter(prefix=path_prefix)

    @router.get("/visualizer", response_class=HTMLResponse)
    async def visualizer_page():
        return _VISUALIZER_HTML

    @router.get("/api/graph")
    async def get_graph_data(
        limit: int = 500,
        labels: str | None = Query(default=None, description="Comma-separated Neo4j labels to include"),
    ):
        client = await get_client()
        driver = client.driver

        if labels:
            label_list = [l.strip() for l in labels.split(",") if l.strip()]
            node_query = """
                MATCH (n)
                WHERE any(l IN labels(n) WHERE l IN $labels)
                RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary,
                       n.created_at AS created_at, labels(n) AS labels,
                       properties(n) AS attrs
                ORDER BY n.created_at DESC
                LIMIT $limit
            """
            records, _, _ = await driver.execute_query(
                node_query, params={"labels": label_list, "limit": limit}
            )
        else:
            node_query = """
                MATCH (n)
                WHERE n.uuid IS NOT NULL
                RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary,
                       n.created_at AS created_at, labels(n) AS labels,
                       properties(n) AS attrs
                ORDER BY n.created_at DESC
                LIMIT $limit
            """
            records, _, _ = await driver.execute_query(
                node_query, params={"limit": limit}
            )

        nodes = []
        node_uuids: set[str] = set()
        for record in records:
            uuid = record["uuid"]
            if uuid is None:
                continue
            node_uuids.add(uuid)
            node_labels = list(record["labels"] or [])
            attrs = _sanitize_attrs(dict(record["attrs"] or {}))

            # Pick the most specific label (not "Entity" or "__Entity__") for coloring.
            specific_labels = [l for l in node_labels if l not in ("Entity", "__Entity__")]
            group = specific_labels[0] if specific_labels else (node_labels[0] if node_labels else "Unknown")

            summary = record["summary"] or ""
            title_parts = [f"<b>{record['name'] or uuid}</b>"]
            if summary:
                title_parts.append(f"<i>{summary[:120]}</i>")
            file_path = attrs.get("file_path")
            if file_path:
                title_parts.append(f"File: {file_path}")

            nodes.append({
                "id": uuid,
                "label": record["name"] or uuid[:8],
                "group": group,
                "title": "<br>".join(title_parts),
                "created_at": _safe_value(record["created_at"]),
                "nodeLabels": node_labels,
            })

        # Fetch edges between the returned nodes.
        edges = []
        if node_uuids:
            edge_query = """
                MATCH (n)-[r]->(m)
                WHERE n.uuid IN $uuids AND m.uuid IN $uuids
                RETURN r.uuid AS uuid, n.uuid AS source, m.uuid AS target,
                       type(r) AS rel_type, r.name AS name, r.fact AS fact,
                       r.created_at AS created_at
            """
            edge_records, _, _ = await driver.execute_query(
                edge_query, params={"uuids": list(node_uuids)}
            )
            for er in edge_records:
                edge_id = er["uuid"] or f"{er['source']}-{er['target']}"
                edges.append({
                    "id": edge_id,
                    "from": er["source"],
                    "to": er["target"],
                    "label": er["name"] or er["rel_type"] or "",
                    "title": er["fact"] or er["name"] or er["rel_type"] or "",
                    "relType": er["rel_type"],
                    "created_at": _safe_value(er["created_at"]),
                })

        # Build color map for all groups present.
        groups = {n["group"] for n in nodes}
        color_map = {g: _label_color(g, color_overrides) for g in groups}

        return {
            "nodes": nodes,
            "edges": edges,
            "colorMap": color_map,
            "defaultUnchecked": list(DEFAULT_UNCHECKED),
        }

    @router.get("/api/graph/labels")
    async def get_graph_labels():
        client = await get_client()
        driver = client.driver
        records, _, _ = await driver.execute_query("CALL db.labels() YIELD label RETURN label")
        labels = [r["label"] for r in records]
        color_map = {l: _label_color(l, color_overrides) for l in labels}
        return {
            "labels": labels,
            "colorMap": color_map,
            "defaultUnchecked": list(DEFAULT_UNCHECKED),
        }

    @router.get("/api/graph/node/{uuid}")
    async def get_node_detail(request: Request, uuid: str):
        client = await get_client()
        driver = client.driver

        node_query = """
            MATCH (n {uuid: $uuid})
            RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary,
                   labels(n) AS labels, properties(n) AS attrs,
                   n.created_at AS created_at
        """
        records, _, _ = await driver.execute_query(node_query, params={"uuid": uuid})
        if not records:
            return {"error": "Node not found"}

        record = records[0]
        attrs = _sanitize_attrs(dict(record["attrs"] or {}))
        # Remove fields already shown at the top level.
        for key in ("uuid", "name", "group_id", "summary", "created_at"):
            attrs.pop(key, None)

        edge_query = """
            MATCH (n {uuid: $uuid})-[r]-(m)
            WHERE m.uuid IS NOT NULL
            RETURN type(r) AS rel_type, r.name AS edge_name, r.fact AS fact,
                   m.name AS connected_name, m.uuid AS connected_uuid,
                   CASE WHEN startNode(r).uuid = $uuid THEN 'outgoing' ELSE 'incoming' END AS direction
            LIMIT 20
        """
        edge_records, _, _ = await driver.execute_query(edge_query, params={"uuid": uuid})

        connections = [
            {
                "relType": er["rel_type"],
                "edgeName": er["edge_name"] or er["rel_type"],
                "fact": er["fact"],
                "connectedName": er["connected_name"],
                "connectedUuid": er["connected_uuid"],
                "direction": er["direction"],
            }
            for er in edge_records
        ]

        out: dict[str, Any] = {
            "uuid": record["uuid"],
            "name": record["name"],
            "labels": list(record["labels"] or []),
            "summary": record["summary"],
            "created_at": _safe_value(record["created_at"]),
            "attributes": attrs,
            "connections": connections,
        }

        if feedback_state_attr:
            fb = getattr(request.app.state, feedback_state_attr, None)
            getter = getattr(fb, "get_confidence_detail", None) if fb is not None else None
            if callable(getter):
                out["reboot_confidence"] = await getter(uuid)

        return out

    return router
