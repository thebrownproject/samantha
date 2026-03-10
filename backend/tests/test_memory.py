"""Tests for SQLite schema, migration framework, save, search, and daily logs."""

import sqlite3
from datetime import date

import pytest

from samantha.memory import MIGRATIONS, MemoryStore


async def test_database_creation(tmp_path):
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path=db_path)
    await store.initialize()
    assert db_path.exists()
    await store.close()


async def test_schema_version_set(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    row = store.conn.execute("SELECT version FROM schema_version").fetchone()
    expected = MIGRATIONS[-1][0]
    assert row[0] == expected
    await store.close()


async def test_tables_exist(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    tables = {
        r[0]
        for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memories" in tables
    assert "daily_logs" in tables
    assert "schema_version" in tables
    await store.close()


async def test_memories_columns(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert cols == {"id", "content", "tags", "source", "created_at", "updated_at"}
    await store.close()


async def test_daily_logs_columns(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(daily_logs)").fetchall()}
    assert cols == {"id", "date", "entry", "created_at"}
    await store.close()


async def test_initialize_idempotent(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.close()
    # Re-open and re-initialize -- should not raise or duplicate data
    store2 = MemoryStore(db_path=tmp_path / "memory.db")
    await store2.initialize()
    row = store2.conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == MIGRATIONS[-1][0]
    count = store2.conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1
    await store2.close()


async def test_close(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.close()
    assert store._conn is None


async def test_conn_property_raises_before_init(tmp_path):
    import pytest

    store = MemoryStore(db_path=tmp_path / "memory.db")
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = store.conn


async def test_parent_dir_created(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "memory.db"
    store = MemoryStore(db_path=db_path)
    await store.initialize()
    assert db_path.exists()
    await store.close()


async def test_fts5_table_exists(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    tables = {
        r[0]
        for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memories_fts" in tables
    await store.close()


async def test_vec0_table_exists(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    tables = {
        r[0]
        for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memory_embeddings" in tables
    await store.close()


async def test_embedding_dimensions(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    vec = await store.encode("hello world")
    assert isinstance(vec, list)
    assert len(vec) == 384
    assert all(isinstance(v, float) for v in vec)
    await store.close()


async def test_fts_trigger_insert(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    store.conn.execute(
        "INSERT INTO memories (content, tags) VALUES (?, ?)",
        ("remember this fact", "test,demo"),
    )
    store.conn.commit()
    rows = store.conn.execute(
        "SELECT * FROM memories_fts WHERE memories_fts MATCH ?", ("remember",)
    ).fetchall()
    assert len(rows) == 1
    assert "remember this fact" in rows[0][0]
    await store.close()


async def test_fts_trigger_delete(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    store.conn.execute(
        "INSERT INTO memories (content, tags) VALUES (?, ?)", ("delete me", "temp"),
    )
    store.conn.commit()
    store.conn.execute("DELETE FROM memories WHERE content = 'delete me'")
    store.conn.commit()
    rows = store.conn.execute(
        "SELECT * FROM memories_fts WHERE memories_fts MATCH ?", ("delete",)
    ).fetchall()
    assert len(rows) == 0
    await store.close()


async def test_fts_trigger_update(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    store.conn.execute(
        "INSERT INTO memories (content, tags) VALUES (?, ?)", ("old content", "v1"),
    )
    store.conn.commit()
    store.conn.execute(
        "UPDATE memories SET content = 'new content', tags = 'v2' WHERE content = 'old content'"
    )
    store.conn.commit()
    old = store.conn.execute(
        "SELECT * FROM memories_fts WHERE memories_fts MATCH ?", ("old",)
    ).fetchall()
    new = store.conn.execute(
        "SELECT * FROM memories_fts WHERE memories_fts MATCH ?", ("new",)
    ).fetchall()
    assert len(old) == 0
    assert len(new) == 1
    await store.close()


async def test_v1_to_v2_migration(tmp_path):
    """Initialize with V1 only, then re-init to get V2 applied."""
    db_path = tmp_path / "memory.db"
    # Manually create a V1-only database
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    for sql in MIGRATIONS[0][1]:
        conn.execute(sql)
    conn.commit()
    conn.close()

    # Now open with MemoryStore -- should apply V2
    store = MemoryStore(db_path=db_path)
    await store.initialize()
    row = store.conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == 2
    tables = {
        r[0]
        for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memories_fts" in tables
    assert "memory_embeddings" in tables
    await store.close()


# --- save() tests ---


async def test_save_inserts_into_memories(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    mem_id = await store.save("The user likes jazz music", tags="preference")
    row = store.conn.execute("SELECT content, tags FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert row[0] == "The user likes jazz music"
    assert row[1] == "preference"
    await store.close()


async def test_save_generates_embedding(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    mem_id = await store.save("Embedding test content")
    row = store.conn.execute(
        "SELECT memory_id FROM memory_embeddings WHERE memory_id = ?", (mem_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == mem_id
    await store.close()


async def test_save_returns_valid_id(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    id1 = await store.save("First memory")
    id2 = await store.save("Second memory")
    assert isinstance(id1, int)
    assert id1 > 0
    assert id2 > id1
    await store.close()


async def test_save_deduplication(tmp_path):
    """Near-identical content should update existing row, not insert."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    id1 = await store.save("The user's favorite color is blue", tags="preference")
    # Save nearly identical content
    id2 = await store.save("The user's favorite color is blue", tags="preference,updated")
    assert id1 == id2
    row = store.conn.execute("SELECT tags FROM memories WHERE id = ?", (id1,)).fetchone()
    assert row[0] == "preference,updated"
    count = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert count == 1
    await store.close()


async def test_save_distinct_content_creates_new(tmp_path):
    """Sufficiently different content should create a new entry."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    id1 = await store.save("The user likes jazz music")
    id2 = await store.save("The weather in Tokyo is rainy today")
    assert id1 != id2
    count = store.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert count == 2
    await store.close()


async def test_save_with_source(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    mem_id = await store.save("fact", source="conversation")
    row = store.conn.execute("SELECT source FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert row[0] == "conversation"
    await store.close()


# --- search() tests ---


async def test_search_finds_by_text(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.save("The user's birthday is March 15th", tags="personal")
    await store.save("Meeting with Alice scheduled for Friday", tags="calendar")
    results = await store.search("birthday")
    assert len(results) >= 1
    assert any("birthday" in r["content"] for r in results)
    await store.close()


async def test_search_finds_by_semantic_similarity(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.save("The user enjoys listening to jazz and blues")
    await store.save("Python is a programming language")
    results = await store.search("music preferences")
    assert len(results) >= 1
    # Jazz/blues memory should rank higher than Python memory
    assert "jazz" in results[0]["content"].lower()
    await store.close()


async def test_search_returns_structured_results(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.save("Structured result test", tags="test", source="unit_test")
    results = await store.search("structured result")
    assert len(results) == 1
    r = results[0]
    assert set(r.keys()) == {"id", "content", "tags", "source", "score", "created_at"}
    assert isinstance(r["id"], int)
    assert isinstance(r["score"], float)
    assert r["tags"] == "test"
    assert r["source"] == "unit_test"
    await store.close()


async def test_search_no_matches_returns_empty(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    results = await store.search("quantum entanglement")
    assert results == []
    await store.close()


async def test_search_respects_limit(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    for i in range(5):
        await store.save(f"Memory number {i} about cats and dogs")
    results = await store.search("cats and dogs", limit=3)
    assert len(results) <= 3
    await store.close()


async def test_search_hybrid_scoring(tmp_path):
    """Both FTS and vector should contribute to the final score."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    # This content matches both textually and semantically
    await store.save("The capital of France is Paris")
    # This content is semantically related but uses different words
    await store.save("French geography and major European cities")
    results = await store.search("capital of France")
    assert len(results) >= 1
    # The exact text match should score highest
    assert "capital" in results[0]["content"].lower()
    # Top result should have a meaningful score from both FTS and vec contributions
    assert results[0]["score"] > 0
    await store.close()


# --- daily log tests ---


async def test_append_daily_log(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    log_id = await store.append_daily_log("User asked about the weather", "2026-03-10")
    assert isinstance(log_id, int)
    assert log_id > 0
    row = store.conn.execute("SELECT entry, date FROM daily_logs WHERE id = ?", (log_id,)).fetchone()
    assert row[0] == "User asked about the weather"
    assert row[1] == "2026-03-10"
    await store.close()


async def test_get_daily_log_by_date(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.append_daily_log("Entry one", "2026-03-10")
    await store.append_daily_log("Entry two", "2026-03-10")
    await store.append_daily_log("Different day", "2026-03-11")
    entries = await store.get_daily_log("2026-03-10")
    assert len(entries) == 2
    assert entries[0]["entry"] == "Entry one"
    assert entries[1]["entry"] == "Entry two"
    assert all(e["date"] == "2026-03-10" for e in entries)
    await store.close()


async def test_get_daily_log_returns_structure(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    await store.append_daily_log("Structured check", "2026-03-10")
    entries = await store.get_daily_log("2026-03-10")
    assert len(entries) == 1
    assert set(entries[0].keys()) == {"id", "date", "entry", "created_at"}
    await store.close()


async def test_daily_log_default_date(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    today = date.today().isoformat()
    log_id = await store.append_daily_log("Default date entry")
    row = store.conn.execute("SELECT date FROM daily_logs WHERE id = ?", (log_id,)).fetchone()
    assert row[0] == today
    entries = await store.get_daily_log()
    assert len(entries) == 1
    assert entries[0]["entry"] == "Default date entry"
    await store.close()


async def test_promote_to_memory(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    log_id = await store.append_daily_log("User prefers dark mode", "2026-03-10")
    mem_id = await store.promote_to_memory(log_id, tags="preference")
    row = store.conn.execute(
        "SELECT content, tags, source FROM memories WHERE id = ?", (mem_id,),
    ).fetchone()
    assert row[0] == "User prefers dark mode"
    assert row[1] == "preference"
    assert row[2] == "daily_log"
    await store.close()


async def test_promote_generates_embedding(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    log_id = await store.append_daily_log("Embedding promotion test", "2026-03-10")
    mem_id = await store.promote_to_memory(log_id)
    row = store.conn.execute(
        "SELECT memory_id FROM memory_embeddings WHERE memory_id = ?", (mem_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == mem_id
    await store.close()


async def test_promote_nonexistent_raises(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    with pytest.raises(ValueError, match="not found"):
        await store.promote_to_memory(9999)
    await store.close()
