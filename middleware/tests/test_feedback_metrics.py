import pytest

from middleware.components.feedback_logger import FeedbackLogger
from middleware.models import FeedbackSignal


@pytest.mark.asyncio
async def test_log_query_metrics_records_metrics(tmp_path):
    db_path = tmp_path / "feedback.db"
    feedback_logger = FeedbackLogger(db_path=str(db_path))
    await feedback_logger.init_db()

    metrics = {
        "precision@1": 1.0,
        "precision@3": 0.66,
        "precision@5": 0.4,
        "precision@10": 0.2,
        "mrr": 1.0,
    }

    await feedback_logger.log_query_metrics(
        query_id="q1",
        metrics=metrics,
        signal=FeedbackSignal.positive,
        details="test metrics",
    )

    cursor = await feedback_logger._db.execute(
        "SELECT query_id, precision_at_1, precision_at_3, precision_at_5, precision_at_10, mrr, signal, details FROM query_metrics"
    )
    row = await cursor.fetchone()

    assert row == (
        "q1",
        pytest.approx(1.0),
        pytest.approx(0.66),
        pytest.approx(0.4),
        pytest.approx(0.2),
        pytest.approx(1.0),
        "positive",
        "test metrics",
    )

    await feedback_logger.close()
