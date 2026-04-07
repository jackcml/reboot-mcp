from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client import OpenAIClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config import (
    EdgeReranker,
    EdgeSearchConfig,
    EdgeSearchMethod,
    NodeReranker,
    NodeSearchConfig,
    NodeSearchMethod,
    SearchConfig as GraphitiSearchConfig,
)
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.utils.bulk_utils import RawEpisode

from middleware.config import settings
from middleware.components.search_config import SearchConfig as RebootSearchConfig
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
        llm_config = LLMConfig(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
        )
        embedder_config = OpenAIEmbedderConfig(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            embedding_model=settings.embedding_model,
        )
        _client = Graphiti(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            llm_client=OpenAIClient(config=llm_config),
            embedder=OpenAIEmbedder(config=embedder_config),
            cross_encoder=OpenAIRerankerClient(config=llm_config),
        )
        await _client.build_indices_and_constraints()
    return _client


async def close_graphiti_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None

def _build_graphiti_config(config: RebootSearchConfig, limit:int) -> GraphitiSearchConfig:
    # BFS traverses graph edges to find structurally related nodes — only worth the cost when structural weight is meaningful
    node_methods = [NodeSearchMethod.bm25, NodeSearchMethod.cosine_similarity]
    edge_methods = [EdgeSearchMethod.bm25, EdgeSearchMethod.cosine_similarity]

    if config.structural_weight >= 0.3:
        node_methods.append(NodeSearchMethod.bfs)
        edge_methods.append(EdgeSearchMethod.bfs)
    
    dominant = max(
        ("semantic", config.semantic_weight),
        ("structural", config.structural_weight),
        ("recency", config.recency_weight),
        key=lambda x: x[1],
    )[0]

    if dominant == "recency":
        node_reranker = NodeReranker.episode_mentions
        edge_reranker = EdgeReranker.episode_mentions
    elif dominant == "structural":
        node_reranker = NodeReranker.node_distance
        edge_reranker = EdgeReranker.node_distance
    else:
        node_reranker = NodeReranker.rrf
        edge_reranker = EdgeReranker.rrf

    # sim_min_score: high semantic weight → lower threshold (wider semantic net);                                             
    # high factual/recency → higher threshold (tighter exact-match filter). 
    sim_min_score = round(0.7 - (0.2 * config.semantic_weight), 2)

    # bfs_max_depth scales with structural weight: 0.2 → depth 1, 0.5 → depth 3, 1.0 → depth 5
    bfs_max_depth = max(1, round(config.structural_weight * 5))

    return GraphitiSearchConfig(
        node_config=NodeSearchConfig(
            search_methods=node_methods,
            reranker=node_reranker,
            sim_min_score=sim_min_score,
            bfs_max_depth=bfs_max_depth,
        ),
        edge_config=EdgeSearchConfig(
            search_methods=edge_methods,
            reranker=edge_reranker,
            sim_min_score=sim_min_score,
            bfs_max_depth=bfs_max_depth,
        ),
        limit=limit,
    )

async def find_center_node_uuid(file_path: str) -> str | None:
    """Return the UUID of a graph node whose file_path matches, or None if not found."""
    client = await get_graphiti_client()
    records, _, _ = await client.driver.execute_query(
        "MATCH (n) WHERE n.file_path = $file_path RETURN n.uuid AS uuid LIMIT 1",
        {"file_path": file_path},
    )
    if not records:
        return None
    row = records[0]
    return row.get("uuid") if isinstance(row, dict) else row["uuid"]


async def search_graph(
    query: str,
    config: RebootSearchConfig | None = None,
    file_context: str | None = None,
    num_results: int = 10,
) -> list[dict]:
    client = await get_graphiti_client()

    search_config = (
        _build_graphiti_config(config, num_results)
        if config is not None
        else COMBINED_HYBRID_SEARCH_RRF.model_copy(update={"limit": num_results})
    )

    center_node_uuid = None
    if file_context:
        center_node_uuid = await find_center_node_uuid(file_context)

    results = await client.search_(query, config=search_config, center_node_uuid=center_node_uuid)
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


async def is_graph_empty() -> bool:
    client = await get_graphiti_client()
    # Direct cypher query gives fastest way to confirm empty graph.
    records, _, _ = await client.driver.execute_query(
        "MATCH (n) RETURN count(n) as c"
    )
    if not records:
        return True
    count = records[0].get("c") if isinstance(records[0], dict) else records[0]["c"]
    return int(count) == 0


async def add_code_episodes_bulk(code_nodes: list, group_id: str | None = None) -> int:
    client = await get_graphiti_client()
    raw_episodes: list[RawEpisode] = []

    for cn in code_nodes:
        body_parts = [f"kind: {cn.kind}", f"name: {cn.name}", f"file: {cn.file_path}"]
        if cn.signature:
            body_parts.append(f"signature: {cn.signature}")
        if cn.docstring:
            body_parts.append(f"docstring: {cn.docstring}")
        if cn.methods:
            body_parts.append(f"methods: {', '.join(cn.methods)}")
        if cn.imports:
            body_parts.append(f"imports: {'; '.join(cn.imports)}")
        body_parts.append(f"lines: {cn.start_line}-{cn.end_line}")
        if cn.source:
            body_parts.append(f"source:\n{cn.source}")

        raw_episodes.append(
            RawEpisode(
                name=f"{cn.kind}:{cn.name}",
                content="\n".join(body_parts),
                source=EpisodeType.text,
                source_description=f"{cn.language} {cn.kind} from {cn.file_path}",
                reference_time=datetime.now(timezone.utc),
            )
        )

    if not raw_episodes:
        return 0

    await client.add_episode_bulk(
        raw_episodes,
        group_id=group_id,
        entity_types=ENTITY_TYPES,
    )
    return len(raw_episodes)



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
