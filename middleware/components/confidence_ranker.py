from middleware.components.feedback_logger import FeedbackLogger
from middleware.models import SearchResultItem


class ConfidencePostRanker:
    async def rerank(
        self,
        results: list[SearchResultItem],
        feedback_logger: FeedbackLogger,
    ) -> list[SearchResultItem]:
        reranked: list[SearchResultItem] = []
        for item in results:
            confidence = await feedback_logger.get_confidence(item.node_id)
            reranked.append(
                item.model_copy(
                    update={
                        "score": item.score * confidence,
                        "confidence": confidence,
                    }
                )
            )
        reranked.sort(key=lambda x: x.score, reverse=True)
        return reranked
