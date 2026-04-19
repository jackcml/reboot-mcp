from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client import OpenAIClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.nodes import EpisodeType
from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.utils.bulk_utils import RawEpisode

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


async def search_graph(query: str, num_results: int = 10) -> list[dict]:
    client = await get_graphiti_client()
    search_config = COMBINED_HYBRID_SEARCH_RRF.model_copy(update={"limit": num_results})
    results = await client.search_(query, config=search_config)
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


BULK_CHUNK_SIZE = 50


async def add_code_episodes_bulk(
    code_nodes: list,
    group_id: str | None = None,
    chunk_size: int = BULK_CHUNK_SIZE,
    progress_callback: Optional[Callable[[int, int], Awaitable[None] | None]] = None,
) -> int:
    """Add code nodes via Graphiti's bulk pipeline, chunked.

    Graphiti's add_episode_bulk runs entity extraction, dedupe, and edge
    resolution as one pipeline that only persists at the end. For large repos
    a single call hangs for tens of minutes with no progress and loses all
    work on cancel. Chunking gives partial persistence and lets the caller
    surface progress.
    """
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

    total = len(raw_episodes)
    if total == 0:
        return 0

    processed = 0
    for start in range(0, total, chunk_size):
        chunk = raw_episodes[start : start + chunk_size]
        await client.add_episode_bulk(
            chunk,
            group_id=group_id,
            entity_types=ENTITY_TYPES,
        )
        processed += len(chunk)
        if progress_callback is not None:
            result = progress_callback(processed, total)
            if hasattr(result, "__await__"):
                await result

    return processed



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
