from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

from middleware.config import settings
from middleware.graph.schemas import CodeClass, CodeFunction, CodeModule

_client: Graphiti | None = None

ENTITY_TYPES: dict[str, type] = {
    "CodeFunction": CodeFunction,
    "CodeClass": CodeClass,
    "CodeModule": CodeModule,
}


async def get_graphiti_client() -> Graphiti:
    global _client
    if _client is None:
        _client = Graphiti(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
        )
        await _client.build_indices_and_constraints()
    return _client


async def close_graphiti_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def search_graph(query: str, num_results: int = 10) -> list[dict]:
    client = await get_graphiti_client()
    results = await client.search(query=query, num_results=num_results)
    items: list[dict] = []

    for node, score in zip(results.nodes, results.node_reranker_scores):
        items.append(
            {
                "node_id": node.uuid,
                "name": node.name,
                "content": node.summary or node.name,
                "score": score,
            }
        )

    for edge, score in zip(results.edges, results.edge_reranker_scores):
        items.append(
            {
                "node_id": edge.uuid,
                "name": edge.name,
                "content": edge.fact,
                "score": score,
            }
        )

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:num_results]


async def add_code_episode(
    name: str,
    body: str,
    source_description: str = "code",
    group_id: str | None = None,
) -> None:
    client = await get_graphiti_client()
    await client.add_episode(
        name=name,
        episode_body=body,
        source=EpisodeType.text,
        source_description=source_description,
        reference_time=datetime.now(timezone.utc),
        entity_types=ENTITY_TYPES,
        group_id=group_id,
    )
