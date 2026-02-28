from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from middleware import mcp_tools
from middleware.components.confidence_ranker import ConfidencePostRanker
from middleware.components.feedback_logger import FeedbackLogger
from middleware.components.query_classifier import QueryClassifier
from middleware.components.search_config import SearchConfigSelector
from middleware.config import settings
from middleware.graph.client import close_graphiti_client, get_graphiti_client
from middleware.models import (
    FeedbackRequest,
    IngestRequest,
    SearchRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    await get_graphiti_client()

    feedback_logger = FeedbackLogger()
    await feedback_logger.init_db()

    query_classifier = QueryClassifier()
    search_config_selector = SearchConfigSelector()
    confidence_ranker = ConfidencePostRanker()

    # Inject shared state into the MCP tools module
    mcp_tools.feedback_logger = feedback_logger
    mcp_tools.query_classifier = query_classifier
    mcp_tools.search_config_selector = search_config_selector
    mcp_tools.confidence_ranker = confidence_ranker

    # Store on app.state for REST endpoint access
    app.state.feedback_logger = feedback_logger
    app.state.query_classifier = query_classifier
    app.state.search_config_selector = search_config_selector
    app.state.confidence_ranker = confidence_ranker

    yield

    # --- Shutdown ---
    await feedback_logger.close()
    await close_graphiti_client()


app = FastAPI(title="Reboot MCP", lifespan=lifespan)

# Mount MCP server at /mcp
app.mount("/mcp", mcp_tools.mcp.http_app())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query")
async def query_endpoint(request: SearchRequest):
    return await mcp_tools.reboot_search(
        query=request.query,
        file_context=request.file_context,
    )


@app.post("/feedback")
async def feedback_endpoint(request: FeedbackRequest):
    return await mcp_tools.reboot_feedback(
        query_id=request.query_id,
        signal=request.signal.value,
        details=request.details,
    )


@app.get("/reboot-explain")
async def explain_endpoint(query_id: Optional[str] = None):
    return await mcp_tools.reboot_explain(query_id=query_id)


@app.post("/ingest")
async def ingest_endpoint(request: IngestRequest):
    return await mcp_tools.reboot_ingest(
        repo_path=request.repo_path,
        incremental=request.incremental,
    )


if __name__ == "__main__":
    uvicorn.run("middleware.main:app", host="0.0.0.0", port=settings.server_port, reload=True)
