import uuid
from collections import OrderedDict
from typing import Optional

from fastmcp import FastMCP

from middleware.components.confidence_ranker import ConfidencePostRanker
from middleware.components.feedback_logger import FeedbackLogger
from middleware.components.query_classifier import QueryClassifier
from middleware.components.search_config import SearchConfigSelector
from middleware.graph.client import search_graph
from middleware.ingestion.parser import ingest_to_graph
from middleware.models import (
    ExplainResponse,
    FeedbackSignal,
    QueryRecord,
    QueryType,
    SearchResponse,
    SearchResultItem,
)

mcp = FastMCP("reboot")

MAX_QUERY_LOG = 1000

# Shared state — set by main.py at startup
feedback_logger: FeedbackLogger | None = None
query_classifier: QueryClassifier | None = None
search_config_selector: SearchConfigSelector | None = None
confidence_ranker: ConfidencePostRanker | None = None
query_log: OrderedDict[str, QueryRecord] = OrderedDict()


def _trim_query_log() -> None:
    while len(query_log) > MAX_QUERY_LOG:
        query_log.popitem(last=False)


@mcp.tool()
async def reboot_search(query: str, file_context: Optional[str] = None) -> dict:
    """Search the codebase knowledge graph.

    Classifies the query, selects search weights, searches the graph,
    and reranks results using confidence scores from feedback history.

    Args:
        query: Natural-language question about the codebase.
        file_context: Optional file path the user is currently viewing.
    """
    assert query_classifier is not None
    assert search_config_selector is not None
    assert confidence_ranker is not None
    assert feedback_logger is not None

    query_type = await query_classifier.classify(query)
    config = search_config_selector.select(query_type)

    raw_results = await search_graph(query)

    result_items = [
        SearchResultItem(
            node_id=r["node_id"],
            name=r["name"],
            content=r["content"],
            score=r["score"],
        )
        for r in raw_results
    ]

    reranked = await confidence_ranker.rerank(result_items, feedback_logger)

    query_id = str(uuid.uuid4())
    weights = {
        "semantic": config.semantic_weight,
        "recency": config.recency_weight,
        "structural": config.structural_weight,
    }

    record = QueryRecord(
        query_id=query_id,
        query=query,
        query_type=query_type,
        weights=weights,
        results=reranked,
    )
    query_log[query_id] = record
    _trim_query_log()

    response = SearchResponse(
        query_id=query_id,
        query_type=query_type,
        results=reranked,
    )
    return response.model_dump()


@mcp.tool()
async def reboot_feedback(
    query_id: str,
    signal: str,
    details: Optional[str] = None,
) -> dict:
    """Submit feedback on search results to improve future ranking.

    Args:
        query_id: The query_id returned from reboot_search.
        signal: "positive" or "negative".
        details: Optional free-text explanation.
    """
    assert feedback_logger is not None

    feedback_signal = FeedbackSignal(signal)

    node_ids: list[str] = []
    record = query_log.get(query_id)
    if record:
        node_ids = [r.node_id for r in record.results]

    await feedback_logger.log_feedback(
        query_id=query_id,
        signal=feedback_signal,
        node_ids=node_ids,
        details=details,
    )

    for nid in node_ids:
        await feedback_logger.update_confidence(nid, feedback_signal)

    return {"status": "ok", "query_id": query_id, "signal": signal}


@mcp.tool()
async def reboot_explain(query_id: Optional[str] = None) -> dict:
    """Explain how the last (or a specific) search was classified and weighted.

    Args:
        query_id: Optional query_id. If omitted, explains the most recent query.
    """
    if query_id is None:
        if not query_log:
            return {"error": "No queries recorded yet."}
        query_id = next(reversed(query_log))

    record = query_log.get(query_id)
    if record is None:
        return {"error": f"Query {query_id} not found in recent history."}

    response = ExplainResponse(
        query_id=record.query_id,
        query=record.query,
        query_type=record.query_type,
        weights=record.weights,
        results=record.results,
    )
    return response.model_dump()


@mcp.tool()
async def reboot_ingest(repo_path: str, incremental: bool = False) -> dict:
    """Ingest a repository into the knowledge graph.

    Parses Python and JavaScript files using tree-sitter, extracts
    functions, classes, and modules, and stores them as Graphiti episodes.

    Args:
        repo_path: Absolute path to the repository root.
        incremental: Reserved for future incremental ingestion support.
    """
    count = await ingest_to_graph(repo_path=repo_path)
    return {"status": "ok", "episodes_added": count}
