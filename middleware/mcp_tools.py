import json
import logging
import uuid
from collections import OrderedDict
from typing import Any, Optional

from fastmcp import FastMCP

from middleware.components.confidence_ranker import ConfidencePostRanker
from middleware.components.feedback_logger import FeedbackLogger
from middleware.components.query_classifier import QueryClassifier
from middleware.components.retrieval_metrics import evaluate_query
from middleware.components.search_config import SearchConfigSelector
from middleware.graph.client import search_graph
from middleware.ingestion.parser import ingest_to_graph, get_ingest_job_status, cancel_ingest_job, set_ingest_task, cancel_ingest_job
from middleware.models import (
    ExplainResponse,
    FeedbackSignal,
    IngestStatus,
    QueryRecord,
    QueryType,
    SearchResponse,
    SearchResultItem,
)

mcp = FastMCP("reboot")

logger = logging.getLogger(__name__)

MAX_QUERY_LOG = 1000
_MAX_LOG_CONTENT_CHARS = 4000

# Shared state — set by main.py at startup
feedback_logger: FeedbackLogger | None = None
query_classifier: QueryClassifier | None = None
search_config_selector: SearchConfigSelector | None = None
confidence_ranker: ConfidencePostRanker | None = None
query_log: OrderedDict[str, QueryRecord] = OrderedDict()


def _trim_query_log() -> None:
    while len(query_log) > MAX_QUERY_LOG:
        query_log.popitem(last=False)


def _truncate_for_log(text: str, max_chars: int = _MAX_LOG_CONTENT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 20]}\n... [{len(text) - max_chars + 20} more chars truncated]"


def _log_reboot_search_report(
    *,
    query_id: str,
    query: str,
    file_context: Optional[str],
    query_type: QueryType,
    weights: dict[str, float],
    raw_results: list[dict[str, Any]],
    reranked: list[SearchResultItem],
    response_dict: dict[str, Any],
) -> None:
    lines: list[str] = [
        "",
        "=" * 72,
        "reboot_search (MCP tool)",
        "=" * 72,
        f"query_id:       {query_id}",
        f"query_type:    {query_type.value}",
        f"file_context:  {file_context!r}",
        f"weights:       semantic={weights['semantic']:.2f} recency={weights['recency']:.2f} structural={weights['structural']:.2f}",
        "",
        "query:",
        _truncate_for_log(query, max_chars=8000),
        "",
        f"graph hits (pre-confidence): {len(raw_results)}",
    ]
    for i, r in enumerate(raw_results, start=1):
        content = str(r.get("content", ""))
        lines.append(
            f"  [{i}] node_id={r.get('node_id')} score={r.get('score')} name={r.get('name')!r}"
        )
        lines.append(f"      content: {_truncate_for_log(content)}")
    lines.append("")
    lines.append(f"results returned (post-confidence rerank): {len(reranked)}")
    for i, r in enumerate(reranked, start=1):
        lines.append(
            f"  [{i}] node_id={r.node_id} graph_score={r.score:.6f} confidence={r.confidence:.6f} name={r.name!r}"
        )
        lines.append(f"      content: {_truncate_for_log(r.content)}")
    lines.append("")
    lines.append("response JSON (same payload sent to MCP client):")
    try:
        lines.append(_truncate_for_log(json.dumps(response_dict, indent=2, default=str), max_chars=120_000))
    except (TypeError, ValueError):
        lines.append(_truncate_for_log(str(response_dict), max_chars=120_000))
    lines.append("=" * 72)
    logger.info("\n".join(lines))


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

    logger.info("reboot_search invoked | query_len=%d file_context=%r", len(query), file_context)

    query_type = await query_classifier.classify(query)
    config = search_config_selector.select(query_type)

    raw_results = await search_graph(query, config=config, file_context=file_context)

    seen_ids = [r["node_id"] for r in raw_results if r.get("node_id")]
    await feedback_logger.touch_nodes_seen_in_results(seen_ids)

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
    out = response.model_dump()
    _log_reboot_search_report(
        query_id=query_id,
        query=query,
        file_context=file_context,
        query_type=query_type,
        weights=weights,
        raw_results=raw_results,
        reranked=reranked,
        response_dict=out,
    )
    return out


@mcp.tool()
async def reboot_feedback(
    query_id: str,
    signal: str,
    node_ids: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> dict:
    """Submit feedback on search results to improve future ranking.

    Args:
        query_id: The query_id returned from reboot_search.
        signal: "positive" or "negative".
        node_ids: Optional list of specific node_ids to target. If omitted, feedback applies to all results from the query.
        details: Optional free-text explanation.
    """
    assert feedback_logger is not None

    feedback_signal = FeedbackSignal(signal)

    record = query_log.get(query_id)
    all_node_ids = [r.node_id for r in record.results] if record else []

    if node_ids:
        valid = set(all_node_ids)
        target_ids = [nid for nid in node_ids if nid in valid]
    else:
        target_ids = all_node_ids

    await feedback_logger.log_feedback(
        query_id=query_id,
        signal=feedback_signal,
        node_ids=target_ids,
        details=details,
    )

    if record and all_node_ids:
        metrics = evaluate_query(record.results, all_node_ids)
        await feedback_logger.log_query_metrics(
            query_id=query_id,
            metrics=metrics,
            signal=feedback_signal,
            details=details,
        )
    else:
        metrics = None

    for nid in target_ids:
        await feedback_logger.update_confidence(nid, feedback_signal)

    response = {"status": "ok", "query_id": query_id, "signal": signal, "nodes_updated": len(target_ids)}
    if metrics is not None:
        response["metrics"] = metrics
    return response


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
async def reboot_ingest(repo_path: str, incremental: bool = False, verbose: bool = False) -> dict:
    """Ingest a repository into the knowledge graph.

    Args:
        repo_path: Absolute path to the repository root.
        incremental: If true, only changed files are ingested.
        verbose: If true, print progress updates to server console every 30s.

    Returns an async job handle that can be polled through reboot_ingest_status.

    Note: this method returns immediately and runs ingestion in background to prevent MCP client timeouts.
    """
    job_id = str(uuid.uuid4())

    # Start async ingestion in background
    async def _background_ingest():
        try:
            await ingest_to_graph(
                repo_path=repo_path,
                incremental=incremental,
                use_bulk_first=True,
                job_id=job_id,
                verbose=verbose,
                feedback_logger=feedback_logger,
            )
        except Exception:
            # ingest_to_graph already records failure state on exception
            pass

    # schedule the ingestion job and return job_id immediately
    import asyncio

    task = asyncio.create_task(_background_ingest())
    set_ingest_task(job_id, task)
    # Keep a reference so it is not garbage-collected until completion.
    # (Optional: we could store in a global job metadata structure if needed.)
    _ = task

    return {"status": "started", "job_id": job_id, "message": "Ingest job started"}


@mcp.tool()
async def reboot_ingest_status(job_id: str) -> dict:
    """Report ingest job status."""
    status = get_ingest_job_status(job_id)
    if status is None:
        return {"status": "not_found", "job_id": job_id}
    return status


@mcp.tool()
async def reboot_ingest_cancel(job_id: str) -> dict:
    """Cancel an ongoing ingest job."""
    cancelled = cancel_ingest_job(job_id)
    if cancelled:
        return {"status": "cancelled", "job_id": job_id, "message": "Ingest job cancelled"}
    else:
        return {"status": "not_cancelled", "job_id": job_id, "message": "Job not found or already completed"}
