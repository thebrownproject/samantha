"""SQLite + FTS5 + sqlite-vec memory system."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".samantha" / "memory.db"


class MemoryStore:
    """Persistent memory with full-text and vector search."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        """Create tables and load extensions."""
        # import sqlite3
        # import sqlite_vec
        # from sentence_transformers import SentenceTransformer
        ...

    async def save(self, content: str, metadata: dict | None = None) -> int:
        """Save a memory entry. Returns the entry ID."""
        ...
        return 0

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories by text and vector similarity."""
        ...
        return []
