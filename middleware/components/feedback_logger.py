import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from middleware.config import settings
from middleware.models import FeedbackSignal

_CONFIDENCE_FLOOR = 0.1
_INGEST_META_KEY = "last_ingest_completed_at"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_ts(dt: datetime) -> str:
    """Store as naive UTC string for SQLite."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


class FeedbackLogger:
    def __init__(
        self,
        db_path: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db_path = db_path or settings.sqlite_path
        self._db: aiosqlite.Connection | None = None
        self._clock = clock or _utc_now

    async def init_db(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                signal TEXT NOT NULL,
                node_ids TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS node_confidence (
                node_id TEXT PRIMARY KEY,
                confidence REAL NOT NULL DEFAULT 1.0,
                last_reinforced_at TIMESTAMP,
                decay_anchor_at TIMESTAMP
            )
            """
        )
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await self._ensure_last_reinforced_column()
        await self._ensure_decay_anchor_column()
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS query_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT NOT NULL,
                precision_at_1 REAL NOT NULL,
                precision_at_3 REAL NOT NULL,
                precision_at_5 REAL NOT NULL,
                precision_at_10 REAL NOT NULL,
                mrr REAL NOT NULL,
                signal TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self._db.commit()

    async def _ensure_last_reinforced_column(self) -> None:
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(node_confidence)")
        rows = await cursor.fetchall()
        colnames = {r[1] for r in rows}
        if "last_reinforced_at" not in colnames:
            await self._db.execute(
                "ALTER TABLE node_confidence ADD COLUMN last_reinforced_at TIMESTAMP"
            )

    async def _ensure_decay_anchor_column(self) -> None:
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(node_confidence)")
        rows = await cursor.fetchall()
        colnames = {r[1] for r in rows}
        if "decay_anchor_at" not in colnames:
            await self._db.execute(
                "ALTER TABLE node_confidence ADD COLUMN decay_anchor_at TIMESTAMP"
            )

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def log_feedback(
        self,
        query_id: str,
        signal: FeedbackSignal,
        node_ids: list[str],
        details: str | None = None,
    ) -> None:
        assert self._db is not None
        ids_str = ",".join(node_ids)
        await self._db.execute(
            "INSERT INTO feedback_events (query_id, signal, node_ids, details) VALUES (?, ?, ?, ?)",
            (query_id, signal.value, ids_str, details),
        )
        await self._db.commit()

    async def record_last_ingest_completed(self) -> None:
        """Call when a full ingest finishes successfully (enables global decay for untracked nodes)."""
        assert self._db is not None
        ts = _format_ts(self._clock())
        await self._db.execute(
            """
            INSERT INTO ingest_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (_INGEST_META_KEY, ts),
        )
        await self._db.commit()

    async def _get_last_ingest_completed(self) -> datetime | None:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM ingest_meta WHERE key = ?",
            (_INGEST_META_KEY,),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        return _parse_ts(str(row[0]))
        
    async def log_query_metrics(
        self,
        query_id: str,
        metrics: dict[str, float],
        signal: FeedbackSignal,
        details: str | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO query_metrics (query_id, precision_at_1, precision_at_3, precision_at_5, precision_at_10, mrr, signal, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                query_id,
                metrics.get("precision@1", 0.0),
                metrics.get("precision@3", 0.0),
                metrics.get("precision@5", 0.0),
                metrics.get("precision@10", 0.0),
                metrics.get("mrr", 0.0),
                signal.value,
                details,
            ),
        )
        await self._db.commit()

    async def get_confidence(self, node_id: str) -> float:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM ingest_meta WHERE key = ?",
            (_INGEST_META_KEY,),
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        return _parse_ts(str(row[0]))

    def _decay_reference(
        self,
        decay_anchor_at: datetime | None,
        last_reinforced_at: datetime | None,
        last_ingest_completed: datetime | None,
    ) -> datetime | None:
        """Clock start for exponential decay: per-node anchor, else last positive, else global ingest."""
        if decay_anchor_at is not None:
            return decay_anchor_at
        if last_reinforced_at is not None:
            return last_reinforced_at
        return last_ingest_completed

    def _effective_from_stored(self, stored: float, decay_ref: datetime | None) -> float:
        if decay_ref is None:
            return max(stored, _CONFIDENCE_FLOOR)
        now = self._clock()
        reference = now + timedelta(days=settings.demo_time_offset_days)
        days_elapsed = max(0.0, (reference - decay_ref).total_seconds() / 86400.0)
        decay_factor = math.exp(-settings.confidence_decay_lambda * days_elapsed)
        return max(stored * decay_factor, _CONFIDENCE_FLOOR)

    async def _fetch_raw_row(
        self, node_id: str
    ) -> tuple[float, datetime | None, datetime | None] | None:
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT confidence, last_reinforced_at, decay_anchor_at
            FROM node_confidence WHERE node_id = ?
            """,
            (node_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        stored = float(row[0])
        last_raw, decay_raw = row[1], row[2]
        last_parsed = (
            None
            if last_raw is None or last_raw == ""
            else _parse_ts(str(last_raw))
        )
        decay_parsed = (
            None
            if decay_raw is None or decay_raw == ""
            else _parse_ts(str(decay_raw))
        )
        return (stored, last_parsed, decay_parsed)

    async def get_confidence(self, node_id: str) -> float:
        assert self._db is not None
        global_ingest = await self._get_last_ingest_completed()
        raw = await self._fetch_raw_row(node_id)
        if raw is None:
            stored = 1.0
            decay_ref = global_ingest
            return self._effective_from_stored(stored, decay_ref)
        stored, last_parsed, decay_parsed = raw
        decay_ref = self._decay_reference(decay_parsed, last_parsed, global_ingest)
        return self._effective_from_stored(stored, decay_ref)

    async def touch_nodes_seen_in_results(self, node_ids: list[str]) -> None:
        """First time a node appears in search results, set decay_anchor_at so it ages like peers."""
        if not node_ids:
            return
        assert self._db is not None
        ts = _format_ts(self._clock())
        for nid in node_ids:
            await self._db.execute(
                """
                INSERT INTO node_confidence (node_id, confidence, last_reinforced_at, decay_anchor_at)
                VALUES (?, 1.0, NULL, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    decay_anchor_at = COALESCE(node_confidence.decay_anchor_at, excluded.decay_anchor_at)
                """,
                (nid, ts),
            )
        await self._db.commit()

    async def get_confidence_detail(self, node_id: str) -> dict[str, Any]:
        """Snapshot for APIs/UI (e.g. graph visualizer): stored vs effective and decay inputs."""
        assert self._db is not None
        global_ingest = await self._get_last_ingest_completed()
        raw = await self._fetch_raw_row(node_id)
        last_ingest_iso = (
            global_ingest.astimezone(timezone.utc).isoformat()
            if global_ingest
            else None
        )
        if raw is None:
            decay_ref = global_ingest
            effective = self._effective_from_stored(1.0, decay_ref)
            return {
                "tracked": False,
                "stored": None,
                "effective": effective,
                "last_reinforced_at": None,
                "decay_anchor_at": None,
                "decay_reference_used": (
                    "last_ingest_completed_at"
                    if global_ingest
                    else "none (effective 1.0 until ingest or first search touch)"
                ),
                "last_ingest_completed_at": last_ingest_iso,
                "confidence_decay_lambda": settings.confidence_decay_lambda,
                "demo_time_offset_days": settings.demo_time_offset_days,
            }
        stored, last_parsed, decay_parsed = raw
        decay_ref = self._decay_reference(decay_parsed, last_parsed, global_ingest)
        effective = self._effective_from_stored(stored, decay_ref)
        if decay_parsed is not None:
            ref_label = "decay_anchor_at"
        elif last_parsed is not None:
            ref_label = "last_reinforced_at"
        elif global_ingest is not None:
            ref_label = "last_ingest_completed_at"
        else:
            ref_label = "none"
        return {
            "tracked": True,
            "stored": stored,
            "effective": effective,
            "last_reinforced_at": (
                last_parsed.astimezone(timezone.utc).isoformat()
                if last_parsed
                else None
            ),
            "decay_anchor_at": (
                decay_parsed.astimezone(timezone.utc).isoformat()
                if decay_parsed
                else None
            ),
            "decay_reference_used": ref_label,
            "last_ingest_completed_at": last_ingest_iso,
            "confidence_decay_lambda": settings.confidence_decay_lambda,
            "demo_time_offset_days": settings.demo_time_offset_days,
        }

    async def update_confidence(self, node_id: str, signal: FeedbackSignal) -> None:
        assert self._db is not None
        global_ingest = await self._get_last_ingest_completed()
        raw = await self._fetch_raw_row(node_id)
        if raw is None:
            stored = 1.0
            last_parsed, decay_parsed = None, None
            decay_ref = self._decay_reference(None, None, global_ingest)
            effective = self._effective_from_stored(stored, decay_ref)
        else:
            stored, last_parsed, decay_parsed = raw
            decay_ref = self._decay_reference(decay_parsed, last_parsed, global_ingest)
            effective = self._effective_from_stored(stored, decay_ref)

        now = self._clock()
        ts = _format_ts(now)

        if signal == FeedbackSignal.positive:
            new_stored = min(effective * 1.1, 2.0)
            if raw is None:
                await self._db.execute(
                    """
                    INSERT INTO node_confidence (node_id, confidence, last_reinforced_at, decay_anchor_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (node_id, new_stored, ts, ts),
                )
            else:
                await self._db.execute(
                    """
                    UPDATE node_confidence SET confidence = ?, last_reinforced_at = ?, decay_anchor_at = ?
                    WHERE node_id = ?
                    """,
                    (new_stored, ts, ts, node_id),
                )
        else:
            new_stored = max(effective * 0.9, _CONFIDENCE_FLOOR)
            if raw is None:
                await self._db.execute(
                    """
                    INSERT INTO node_confidence (node_id, confidence, last_reinforced_at, decay_anchor_at)
                    VALUES (?, ?, NULL, NULL)
                    """,
                    (node_id, new_stored),
                )
            else:
                await self._db.execute(
                    "UPDATE node_confidence SET confidence = ? WHERE node_id = ?",
                    (new_stored, node_id),
                )
        await self._db.commit()

    async def get_feedback_history(self, query_id: str) -> list[dict]:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT query_id, signal, node_ids, details, created_at FROM feedback_events WHERE query_id = ?",
            (query_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "query_id": r[0],
                "signal": r[1],
                "node_ids": r[2].split(",") if r[2] else [],
                "details": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]
