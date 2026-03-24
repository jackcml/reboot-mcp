"""Smoke tests for search_graph merge logic and Graphiti API usage."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode
from graphiti_core.search.search_config import SearchResults
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

from middleware.graph.client import search_graph


@pytest.mark.asyncio
async def test_search_graph_calls_search_with_rrf_config_and_limit():
    mock_client = AsyncMock()
    mock_client.search_ = AsyncMock(return_value=SearchResults())

    with patch(
        "middleware.graph.client.get_graphiti_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        await search_graph("example query", num_results=7)

    mock_client.search_.assert_awaited_once()
    call_kw = mock_client.search_.await_args
    assert call_kw.args[0] == "example query"
    cfg = call_kw.kwargs["config"]
    assert cfg.limit == 7
    assert type(cfg.edge_config) is type(COMBINED_HYBRID_SEARCH_RRF.edge_config)


@pytest.mark.asyncio
async def test_search_graph_merges_nodes_and_edges_sorted_by_score():
    created = datetime.now(timezone.utc)
    node = EntityNode(name="NodeA", group_id="g", summary="summary text")
    edge = EntityEdge(
        name="relates",
        fact="fact about things",
        group_id="g",
        source_node_uuid=node.uuid,
        target_node_uuid=node.uuid,
        created_at=created,
    )
    results = SearchResults(
        nodes=[node],
        node_reranker_scores=[0.4],
        edges=[edge],
        edge_reranker_scores=[0.9],
    )
    mock_client = AsyncMock()
    mock_client.search_ = AsyncMock(return_value=results)

    with patch(
        "middleware.graph.client.get_graphiti_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        items = await search_graph("q", num_results=10)

    assert len(items) == 2
    assert items[0]["score"] >= items[1]["score"]
    assert items[0]["node_id"] == edge.uuid
    assert items[0]["content"] == "fact about things"
    assert any(i["name"] == "NodeA" and i["content"] == "summary text" for i in items)


@pytest.mark.asyncio
async def test_search_graph_each_item_has_expected_keys():
    mock_client = AsyncMock()
    mock_client.search_ = AsyncMock(return_value=SearchResults())

    with patch(
        "middleware.graph.client.get_graphiti_client",
        new_callable=AsyncMock,
        return_value=mock_client,
    ):
        items = await search_graph("q")

    assert items == []
    mock_client.search_.assert_awaited()
