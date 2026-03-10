"""SQLite + FTS5 + sqlite-vec memory system."""

from __future__ import annotations

import asyncio
import sqlite3
import struct
from datetime import date
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

    async def save(
        self, content: str, tags: str | None = None, source: str | None = None,
    ) -> int:
        """Save a memory entry with deduplication. Returns the entry ID."""
        embedding = await self.encode(content)
        raw_vec = _serialize_f32(embedding)

        def _save_sync() -> int:
            # Dedup: check for near-identical content (cosine sim > 0.95 ~ distance < 0.05)
            dupes = self.conn.execute(
                """SELECT memory_id, distance
                   FROM memory_embeddings
                   WHERE embedding MATCH ? AND k = 1""",
                (raw_vec,),
            ).fetchall()
            if dupes and dupes[0][1] < 0.05:
                existing_id = dupes[0][0]
                self.conn.execute(
                    """UPDATE memories
                       SET content = ?, tags = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (content, tags, source, existing_id),
                )
                # Update embedding
                self.conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ?", (existing_id,),
                )
                self.conn.execute(
                    "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                    (existing_id, raw_vec),
                )
                self.conn.commit()
                return existing_id

            cur = self.conn.execute(
                "INSERT INTO memories (content, tags, source) VALUES (?, ?, ?)",
                (content, tags, source),
            )
            memory_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                (memory_id, raw_vec),
            )
            self.conn.commit()
            return memory_id

        return await asyncio.to_thread(_save_sync)

    SEARCH_FTS_WEIGHT = 0.4
    SEARCH_VEC_WEIGHT = 0.6

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memories by hybrid FTS5 + vector similarity. Returns ranked results."""
        embedding = await self.encode(query)
        raw_vec = _serialize_f32(embedding)
        # Sanitize query for FTS5: strip special chars, quote each token
        fts_query = _fts_sanitize(query)

        def _search_sync() -> list[dict]:
            # Vector search: get top candidates (fetch more than limit for merging)
            fetch_n = min(limit * 3, 100)
            vec_rows = self.conn.execute(
                """SELECT memory_id, distance
                   FROM memory_embeddings
                   WHERE embedding MATCH ? AND k = ?""",
                (raw_vec, fetch_n),
            ).fetchall()

            # FTS search
            fts_rows = []
            if fts_query:
                fts_rows = self.conn.execute(
                    """SELECT rowid, rank
                       FROM memories_fts
                       WHERE memories_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (fts_query, fetch_n),
                ).fetchall()

            # Build score maps. Lower distance = better for vec; rank is negative for FTS (more negative = better match)
            vec_scores: dict[int, float] = {}
            if vec_rows:
                max_dist = max(r[1] for r in vec_rows) or 1.0
                for mid, dist in vec_rows:
                    vec_scores[mid] = 1.0 - (dist / max_dist) if max_dist > 0 else 1.0

            fts_scores: dict[int, float] = {}
            if fts_rows:
                # FTS5 rank is negative; more negative = better
                min_rank = min(r[1] for r in fts_rows)
                max_rank = max(r[1] for r in fts_rows)
                span = max_rank - min_rank if max_rank != min_rank else 1.0
                for rid, rank in fts_rows:
                    fts_scores[rid] = (max_rank - rank) / span

            # Merge all candidate IDs
            all_ids = set(vec_scores) | set(fts_scores)
            if not all_ids:
                return []

            scored: list[tuple[int, float]] = []
            for mid in all_ids:
                vs = vec_scores.get(mid, 0.0)
                fs = fts_scores.get(mid, 0.0)
                combined = self.SEARCH_VEC_WEIGHT * vs + self.SEARCH_FTS_WEIGHT * fs
                scored.append((mid, combined))

            scored.sort(key=lambda x: x[1], reverse=True)
            top_ids = [s[0] for s in scored[:limit]]
            score_map = dict(scored)

            if not top_ids:
                return []

            placeholders = ",".join("?" * len(top_ids))
            rows = self.conn.execute(
                f"""SELECT id, content, tags, source, created_at
                    FROM memories WHERE id IN ({placeholders})""",
                top_ids,
            ).fetchall()

            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "tags": row[2],
                    "source": row[3],
                    "score": round(score_map.get(row[0], 0.0), 4),
                    "created_at": row[4],
                })
            results.sort(key=lambda x: x["score"], reverse=True)
            return results

        return await asyncio.to_thread(_search_sync)

    async def append_daily_log(self, entry: str, date_str: str | None = None) -> int:
        """Append an entry to the daily log. Returns the log entry ID."""
        if date_str is None:
            date_str = date.today().isoformat()

        def _append_sync() -> int:
            cur = self.conn.execute(
                "INSERT INTO daily_logs (date, entry) VALUES (?, ?)",
                (date_str, entry),
            )
            self.conn.commit()
            return cur.lastrowid

        return await asyncio.to_thread(_append_sync)

    async def get_daily_log(self, date_str: str | None = None) -> list[dict]:
        """Get all log entries for a given date (defaults to today)."""
        if date_str is None:
            date_str = date.today().isoformat()

        def _get_sync() -> list[dict]:
            rows = self.conn.execute(
                "SELECT id, date, entry, created_at FROM daily_logs WHERE date = ? ORDER BY id",
                (date_str,),
            ).fetchall()
            return [
                {"id": r[0], "date": r[1], "entry": r[2], "created_at": r[3]}
                for r in rows
            ]

        return await asyncio.to_thread(_get_sync)

    async def promote_to_memory(self, daily_log_id: int, tags: str = "") -> int:
        """Promote a daily log entry to permanent memory. Returns the new memory ID."""
        row = self.conn.execute(
            "SELECT entry FROM daily_logs WHERE id = ?", (daily_log_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Daily log entry {daily_log_id} not found")
        tag_str = tags if tags else None
        return await self.save(row[0], tags=tag_str, source="daily_log")


def _serialize_f32(vec: list[float]) -> bytes:
    """Pack a float list into little-endian binary for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _fts_sanitize(query: str) -> str:
    """Convert a natural language query into a safe FTS5 query string."""
    tokens = []
    for word in query.split():
        cleaned = "".join(c for c in word if c.isalnum())
        if cleaned:
            tokens.append(f'"{cleaned}"')
    return " OR ".join(tokens)
