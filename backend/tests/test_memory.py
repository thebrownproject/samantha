"""Tests for SQLite schema and migration framework."""

import sqlite3

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
