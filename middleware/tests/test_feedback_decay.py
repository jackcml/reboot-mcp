"""Tests for lazy exponential confidence decay in FeedbackLogger."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from middleware.components.feedback_logger import FeedbackLogger, _format_ts
from middleware.config import settings
from middleware.models import FeedbackSignal


@pytest.fixture
def fixed_clock():
    return datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def logger(tmp_path, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 0.1)
    monkeypatch.setattr(settings, "demo_time_offset_days", 0)
    db_path = tmp_path / "fb.db"
    fl = FeedbackLogger(db_path=str(db_path), clock=lambda: fixed_clock)
    await fl.init_db()
    return fl


@pytest.mark.asyncio
async def test_get_confidence_missing_node_returns_one(logger):
    assert await logger.get_confidence("unknown") == 1.0


@pytest.mark.asyncio
async def test_null_last_reinforced_no_time_decay(logger):
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES ('n1', 0.85, NULL)
        """
    )
    await logger._db.commit()
    assert await logger.get_confidence("n1") == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_decay_after_positive_reinforcement(logger, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 1.0)
    last = datetime(2025, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES (?, ?, ?)
        """,
        ("n2", 1.0, _format_ts(last)),
    )
    await logger._db.commit()
    days = (fixed_clock - last).total_seconds() / 86400.0
    expected = max(1.0 * math.exp(-1.0 * days), 0.1)
    assert await logger.get_confidence("n2") == pytest.approx(expected)


@pytest.mark.asyncio
async def test_demo_time_offset_increases_decay(logger, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 1.0)
    monkeypatch.setattr(settings, "demo_time_offset_days", 10)
    last = datetime(2025, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES (?, ?, ?)
        """,
        ("n3", 1.0, _format_ts(last)),
    )
    await logger._db.commit()
    ref = fixed_clock + timedelta(days=10)
    days = (ref - last).total_seconds() / 86400.0
    expected = max(1.0 * math.exp(-1.0 * days), 0.1)
    assert await logger.get_confidence("n3") == pytest.approx(expected)


@pytest.mark.asyncio
async def test_update_positive_sets_timestamp_and_uses_effective_baseline(
    logger, fixed_clock, monkeypatch
):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 1.0)
    last = datetime(2025, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES (?, ?, ?)
        """,
        ("n4", 1.0, _format_ts(last)),
    )
    await logger._db.commit()

    effective_before = await logger.get_confidence("n4")
    await logger.update_confidence("n4", FeedbackSignal.positive)

    cursor = await logger._db.execute(
        "SELECT confidence, last_reinforced_at, decay_anchor_at FROM node_confidence WHERE node_id = ?",
        ("n4",),
    )
    row = await cursor.fetchone()
    assert row is not None
    new_stored, new_ts, new_anchor = float(row[0]), row[1], row[2]
    assert new_stored == pytest.approx(min(effective_before * 1.1, 2.0))
    assert str(new_ts).startswith("2025-06-15")
    assert new_anchor is not None
    assert str(new_anchor).startswith("2025-06-15")


@pytest.mark.asyncio
async def test_update_negative_preserves_last_reinforced_at(logger, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 0.0)
    last = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES (?, ?, ?)
        """,
        ("n5", 1.0, _format_ts(last)),
    )
    await logger._db.commit()

    await logger.update_confidence("n5", FeedbackSignal.negative)

    cursor = await logger._db.execute(
        "SELECT confidence, last_reinforced_at FROM node_confidence WHERE node_id = ?",
        ("n5",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert float(row[0]) == pytest.approx(0.9)
    assert row[1] is not None
    assert "2025-01-01" in str(row[1])


@pytest.mark.asyncio
async def test_migration_adds_last_reinforced_column(tmp_path, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 0.0)
    monkeypatch.setattr(settings, "demo_time_offset_days", 0)
    import aiosqlite

    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE node_confidence (
                node_id TEXT PRIMARY KEY,
                confidence REAL NOT NULL DEFAULT 1.0
            )
            """
        )
        await conn.execute(
            "INSERT INTO node_confidence (node_id, confidence) VALUES ('legacy', 1.25)"
        )
        await conn.commit()

    fl = FeedbackLogger(db_path=str(db_path), clock=lambda: fixed_clock)
    await fl.init_db()
    assert await fl.get_confidence("legacy") == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_get_confidence_detail_untracked(logger):
    d = await logger.get_confidence_detail("no_such_node")
    assert d["tracked"] is False
    assert d["stored"] is None
    assert d["effective"] == 1.0
    assert d["last_reinforced_at"] is None
    assert "none" in d["decay_reference_used"]


@pytest.mark.asyncio
async def test_global_ingest_decays_untracked_node(logger, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 1.0)
    assert logger._db is not None
    ingest_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    await logger._db.execute(
        "INSERT INTO ingest_meta (key, value) VALUES ('last_ingest_completed_at', ?)",
        (_format_ts(ingest_dt),),
    )
    await logger._db.commit()
    days = (fixed_clock - ingest_dt).total_seconds() / 86400.0
    expected = max(1.0 * math.exp(-1.0 * days), 0.1)
    got = await logger.get_confidence("never_seen_uuid")
    assert got == pytest.approx(expected, rel=0.01)


@pytest.mark.asyncio
async def test_touch_sets_decay_anchor_for_new_node(logger, fixed_clock, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 0.0)
    await logger.touch_nodes_seen_in_results(["touch1"])
    d = await logger.get_confidence_detail("touch1")
    assert d["tracked"] is True
    assert d["decay_anchor_at"] is not None
    assert d["decay_reference_used"] == "decay_anchor_at"


@pytest.mark.asyncio
async def test_get_confidence_detail_tracked(logger, monkeypatch):
    monkeypatch.setattr(settings, "confidence_decay_lambda", 0.0)
    assert logger._db is not None
    await logger._db.execute(
        """
        INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
        VALUES ('x1', 1.2, '2025-01-01 00:00:00')
        """
    )
    await logger._db.commit()
    d = await logger.get_confidence_detail("x1")
    assert d["tracked"] is True
    assert d["stored"] == pytest.approx(1.2)
    assert d["effective"] == pytest.approx(1.2)
    assert d["last_reinforced_at"] is not None
    assert d["decay_reference_used"] == "last_reinforced_at"
