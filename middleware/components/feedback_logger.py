import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import aiosqlite

from middleware.config import settings
from middleware.models import FeedbackSignal

_CONFIDENCE_FLOOR = 0.1


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
    # SQLite may return "YYYY-MM-DD HH:MM:SS" or ISO
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
                last_reinforced_at TIMESTAMP
            )
            """
        )
        await self._ensure_last_reinforced_column()
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

    def _effective_confidence(
        self,
        stored: float,
        last_reinforced_at: datetime | None,
    ) -> float:
        if last_reinforced_at is None:
            decay_factor = 1.0
        else:
            now = self._clock()
            reference = now + timedelta(days=settings.demo_time_offset_days)
            lr = last_reinforced_at
            days_elapsed = max(0.0, (reference - lr).total_seconds() / 86400.0)
            decay_factor = math.exp(-settings.confidence_decay_lambda * days_elapsed)
        return max(stored * decay_factor, _CONFIDENCE_FLOOR)

    async def _fetch_raw_row(
        self, node_id: str
    ) -> tuple[float, datetime | None] | None:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT confidence, last_reinforced_at FROM node_confidence WHERE node_id = ?",
            (node_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        stored = float(row[0])
        last_raw = row[1]
        last_parsed: datetime | None
        if last_raw is None or last_raw == "":
            last_parsed = None
        else:
            last_parsed = _parse_ts(str(last_raw))
        return (stored, last_parsed)

    async def get_confidence(self, node_id: str) -> float:
        assert self._db is not None
        raw = await self._fetch_raw_row(node_id)
        if raw is None:
            return 1.0
        stored, last_parsed = raw
        return self._effective_confidence(stored, last_parsed)

    async def update_confidence(self, node_id: str, signal: FeedbackSignal) -> None:
        assert self._db is not None
        raw = await self._fetch_raw_row(node_id)
        if raw is None:
            effective = 1.0
        else:
            stored, last_parsed = raw
            effective = self._effective_confidence(stored, last_parsed)

        if signal == FeedbackSignal.positive:
            new_stored = min(effective * 1.1, 2.0)
            now = self._clock()
            ts = _format_ts(now)
            if raw is None:
                await self._db.execute(
                    """
                    INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
                    VALUES (?, ?, ?)
                    """,
                    (node_id, new_stored, ts),
                )
            else:
                await self._db.execute(
                    """
                    UPDATE node_confidence SET confidence = ?, last_reinforced_at = ?
                    WHERE node_id = ?
                    """,
                    (new_stored, ts, node_id),
                )
        else:
            new_stored = max(effective * 0.9, _CONFIDENCE_FLOOR)
            if raw is None:
                await self._db.execute(
                    """
                    INSERT INTO node_confidence (node_id, confidence, last_reinforced_at)
                    VALUES (?, ?, NULL)
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
