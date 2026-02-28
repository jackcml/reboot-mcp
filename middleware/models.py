from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QueryType(str, Enum):
    conceptual = "conceptual"
    procedural = "procedural"
    factual = "factual"


class SearchRequest(BaseModel):
    query: str
    file_context: Optional[str] = None


class SearchResultItem(BaseModel):
    node_id: str
    name: str
    content: str
    score: float
    confidence: float = 1.0


class SearchResponse(BaseModel):
    query_id: str
    query_type: QueryType
    results: list[SearchResultItem]


class FeedbackSignal(str, Enum):
    positive = "positive"
    negative = "negative"


class FeedbackRequest(BaseModel):
    query_id: str
    signal: FeedbackSignal
    node_ids: list[str] = Field(default_factory=list)
    details: Optional[str] = None


class ExplainResponse(BaseModel):
    query_id: str
    query: str
    query_type: QueryType
    weights: dict[str, float]
    results: list[SearchResultItem]


class IngestRequest(BaseModel):
    repo_path: str
    incremental: bool = False


class QueryRecord(BaseModel):
    query_id: str
    query: str
    query_type: QueryType
    weights: dict[str, float]
    results: list[SearchResultItem]
