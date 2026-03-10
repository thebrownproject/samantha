"""SQLite + FTS5 + sqlite-vec memory system."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import sqlite_vec

DEFAULT_DB_PATH = Path.home() / ".samantha" / "memory.db"

MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            content TEXT NOT NULL,
            tags TEXT,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS daily_logs (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            entry TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]),
    (2, [
        """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, tags, content=memories, content_rowid=id)""",
        # Keep FTS in sync with memories table
        """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, tags)
            VALUES (new.id, new.content, new.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags)
            VALUES ('delete', old.id, old.content, old.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags)
            VALUES ('delete', old.id, old.content, old.tags);
            INSERT INTO memories_fts(rowid, content, tags)
            VALUES (new.id, new.content, new.tags);
        END""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings
            USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[384])""",
    ]),
]


class MemoryStore:
    """Persistent memory with full-text and vector search."""

    EMBEDDING_DIM = 384

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._model = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("MemoryStore not initialized -- call initialize() first")
        return self._conn

    async def initialize(self) -> None:
        """Create tables and run pending migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open_and_migrate)

    def _open_and_migrate(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )""")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (0)")
            conn.commit()

        for version, statements in MIGRATIONS:
            if version <= current:
                continue
            try:
                conn.execute("BEGIN")
                for sql in statements:
                    conn.execute(sql)
                conn.execute("UPDATE schema_version SET version = ?", (version,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
        return self._model

    async def encode(self, text: str) -> list[float]:
        """Encode text into an embedding vector."""
        model = self._get_model()
        vec = await asyncio.to_thread(model.encode, text)
        return vec.tolist()

    async def save(self, content: str, metadata: dict | None = None) -> int:
        """Save a memory entry. Returns the entry ID."""
        ...
        return 0

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories by text and vector similarity."""
        ...
        return []
