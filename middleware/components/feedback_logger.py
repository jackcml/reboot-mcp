import aiosqlite

from middleware.config import settings
from middleware.models import FeedbackSignal


class FeedbackLogger:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.sqlite_path
        self._db: aiosqlite.Connection | None = None

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
                confidence REAL NOT NULL DEFAULT 1.0
            )
            """
        )
        await self._db.commit()

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

    async def get_confidence(self, node_id: str) -> float:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT confidence FROM node_confidence WHERE node_id = ?",
            (node_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 1.0

    async def update_confidence(self, node_id: str, signal: FeedbackSignal) -> None:
        assert self._db is not None
        current = await self.get_confidence(node_id)
        if signal == FeedbackSignal.positive:
            new_confidence = min(current * 1.1, 2.0)
        else:
            new_confidence = max(current * 0.9, 0.1)
        await self._db.execute(
            """
            INSERT INTO node_confidence (node_id, confidence) VALUES (?, ?)
            ON CONFLICT(node_id) DO UPDATE SET confidence = ?
            """,
            (node_id, new_confidence, new_confidence),
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
