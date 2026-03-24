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


class IngestStatus(BaseModel):
    job_id: str
    repo_path: str
    incremental: bool
    stage: str
    total_files: int
    processed_files: int
    total_nodes: int
    processed_nodes: int
    episodes_added: int
    start_time: str
    end_time: Optional[str] = None
    last_update: str
    error: Optional[str] = None
    message: Optional[str] = None


class QueryRecord(BaseModel):
    query_id: str
    query: str
    query_type: QueryType
    weights: dict[str, float]
    results: list[SearchResultItem]
