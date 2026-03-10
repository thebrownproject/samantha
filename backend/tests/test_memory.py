"""Tests for SQLite schema and migration framework."""

import sqlite3

from samantha.memory import MemoryStore, MIGRATIONS


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
    assert "daily_log" in tables
    assert "schema_version" in tables
    await store.close()


async def test_memories_columns(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert cols == {"id", "content", "tags", "source", "created_at", "updated_at"}
    await store.close()


async def test_daily_log_columns(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    await store.initialize()
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(daily_log)").fetchall()}
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
