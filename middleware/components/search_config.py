from dataclasses import dataclass

from middleware.models import QueryType


@dataclass
class SearchConfig:
    semantic_weight: float
    recency_weight: float
    structural_weight: float


RECIPES: dict[QueryType, SearchConfig] = {
    QueryType.architectural: SearchConfig(
        semantic_weight=0.3,
        recency_weight=0.1,
        structural_weight=0.6,
    ),
    QueryType.explanatory: SearchConfig(
        semantic_weight=0.6,
        recency_weight=0.1,
        structural_weight=0.3,
    ),
    QueryType.procedural: SearchConfig(
        semantic_weight=0.3,
        recency_weight=0.2,
        structural_weight=0.5,
    ),
    QueryType.factual: SearchConfig(
        semantic_weight=0.3,
        recency_weight=0.5,
        structural_weight=0.2,
    ),
    QueryType.debugging: SearchConfig(
        semantic_weight=0.2,
        recency_weight=0.5,
        structural_weight=0.3,
    ),
}


class SearchConfigSelector:
    def select(self, query_type: QueryType) -> SearchConfig:
        return RECIPES.get(query_type, RECIPES[QueryType.factual])
