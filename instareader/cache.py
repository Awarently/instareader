"""SQLite result cache keyed by platform+content id. Only successful results are stored."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS results ("
                " key TEXT PRIMARY KEY, url TEXT NOT NULL, created_at TEXT NOT NULL, json TEXT NOT NULL)"
            )
            self._db.commit()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT json FROM results WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, url: str, result: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO results (key, url, created_at, json) VALUES (?, ?, ?, ?)",
                (key, url, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(result)),
            )
            self._db.commit()

    def recent(self, limit: int = 30) -> list[dict]:
        """Newest first: just what a history list needs."""
        with self._lock:
            rows = self._db.execute(
                "SELECT key, url, created_at, json FROM results ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for key, url, created, raw in rows:
            r = json.loads(raw)
            out.append({"id": key, "url": url, "processed_at": created,
                        "title": r.get("title_guess") or "", "creator": r.get("creator") or ""})
        return out

    def delete(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM results WHERE key = ?", (key,))
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
