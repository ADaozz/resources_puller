from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    group_key TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(session_id, group_key)
);
CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    url TEXT NOT NULL,
    mime TEXT,
    width INTEGER,
    height INTEGER,
    label TEXT,
    is_playlist INTEGER NOT NULL DEFAULT 0,
    renditions TEXT,
    created_at REAL NOT NULL,
    UNIQUE(group_id, url)
);
CREATE TABLE IF NOT EXISTS occurrences (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL,
    url TEXT NOT NULL,
    mime TEXT,
    status INTEGER,
    via TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    rendition_index INTEGER,
    url TEXT NOT NULL,
    state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    error TEXT,
    path TEXT,
    filename TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_groups_session ON groups(session_id);
CREATE INDEX IF NOT EXISTS idx_variants_group ON variants(group_id);
CREATE INDEX IF NOT EXISTS idx_occ_variant ON occurrences(variant_id);
CREATE INDEX IF NOT EXISTS idx_downloads_session ON downloads(session_id);
"""


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(variants)")}
        for name, definition in {"source_key": "TEXT", "metadata": "TEXT"}.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE variants ADD COLUMN {name} {definition}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_variant_source ON variants(group_id, source_key)")
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
