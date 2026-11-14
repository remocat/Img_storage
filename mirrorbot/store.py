"""SQLite index of image *references* (IDs only: no bytes, no filenames, no content).

Discord stays the source of truth for the images. We only remember which
message/attachment IDs belong to which pool, so we don't re-scan history on
every request. stdlib sqlite3 runs in a worker thread behind a lock.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    attachment_id INTEGER PRIMARY KEY,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_channel ON images (channel_id, message_id, attachment_id);
CREATE INDEX IF NOT EXISTS idx_images_message ON images (message_id);
"""


@dataclass(frozen=True, slots=True)
class ImageRef:
    attachment_id: int
    channel_id: int
    message_id: int


def _add_many(conn: sqlite3.Connection, rows: list[tuple[int, int, int]]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO images (attachment_id, channel_id, message_id) VALUES (?, ?, ?)", rows
    )


def _remove_messages(conn: sqlite3.Connection, ids: list[tuple[int]]) -> None:
    conn.executemany("DELETE FROM images WHERE message_id = ?", ids)


def _remove_attachment(conn: sqlite3.Connection, attachment_id: int) -> None:
    conn.execute("DELETE FROM images WHERE attachment_id = ?", (attachment_id,))


def _remove_channel(conn: sqlite3.Connection, channel_id: int) -> None:
    conn.execute("DELETE FROM images WHERE channel_id = ?", (channel_id,))


def _counts(conn: sqlite3.Connection) -> dict[int, int]:
    return dict(conn.execute("SELECT channel_id, COUNT(*) FROM images GROUP BY channel_id").fetchall())


def _refs(conn: sqlite3.Connection, channel_id: int, limit: int) -> list[ImageRef]:
    rows = conn.execute(
        "SELECT attachment_id, channel_id, message_id FROM images WHERE channel_id = ? "
        "ORDER BY message_id DESC, attachment_id DESC LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    rows.reverse()  # oldest -> newest
    return [ImageRef(*row) for row in rows]


class ImageStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("ImageStore is not open")
            with self._conn:  # commit on success, rollback on error
                return fn(self._conn, *args)

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(self._call, fn, *args)

    async def add_many(self, refs: Iterable[ImageRef]) -> None:
        rows = [(r.attachment_id, r.channel_id, r.message_id) for r in refs]
        if rows:
            await self._run(_add_many, rows)

    async def remove_messages(self, message_ids: Iterable[int]) -> None:
        rows = [(mid,) for mid in message_ids]
        if rows:
            await self._run(_remove_messages, rows)

    async def remove_attachment(self, attachment_id: int) -> None:
        await self._run(_remove_attachment, attachment_id)

    async def remove_channel(self, channel_id: int) -> None:
        await self._run(_remove_channel, channel_id)

    async def counts(self) -> dict[int, int]:
        return await self._run(_counts)

    async def refs(self, channel_id: int, limit: int) -> list[ImageRef]:
        return await self._run(_refs, channel_id, limit)
