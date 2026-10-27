"""The v014 sense-learning migration is empty, atomic and schema-identical."""
from contextlib import closing
import sqlite3

import pytest
from sqlalchemy import create_engine

from backend import migrations
from backend.models import Base
from backend.tests.test_migrations import legacy  # noqa: F401


TABLES = ("vocabulary_senses", "sense_review_receipts")


def rows(path, sql, parameters=()):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql, parameters).fetchall()


def v014(versions):
    end = next(index for index, item in enumerate(versions) if item[0] == "014_sense_learning") + 1
    return versions[:end]


def test_014_adds_only_empty_tables_preserves_every_existing_row_and_is_repeatable(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = v014(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:-1])
    migrations.migrate_database(engine)
    existing = [row[0] for row in rows(path, "SELECT name FROM sqlite_master WHERE type='table'")
                if row[0] not in {"schema_migrations", "sqlite_sequence"}]
    before = {table: rows(path, f'SELECT * FROM "{table}" ORDER BY 1') for table in existing}

    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    backup = migrations.migrate_database(engine)
    assert backup and "before-014_sense_learning" in backup.name
    assert {table: rows(path, f'SELECT * FROM "{table}" ORDER BY 1') for table in existing} == before
    assert all(rows(path, f'SELECT count(*) FROM "{table}"')[0][0] == 0 for table in TABLES)
    assert migrations.migrate_database(engine) is None


def test_014_failed_ddl_rolls_back_tables_and_version_then_retries(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = v014(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:-1])
    migrations.migrate_database(engine)

    def interrupted(connection, tables):
        migrations._create_sense_learning(connection, tables)
        raise RuntimeError("synthetic v014 interruption")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*versions[:-1], (versions[-1][0], interrupted)])
    with pytest.raises(RuntimeError, match="v014"):
        migrations.migrate_database(engine)
    assert not any(rows(path, "SELECT name FROM sqlite_master WHERE name=?", (table,)) for table in TABLES)
    assert rows(path, "SELECT count(*) FROM schema_migrations WHERE version='014_sense_learning'")[0][0] == 0

    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    assert migrations.migrate_database(engine) is not None
    assert all(rows(path, "SELECT name FROM sqlite_master WHERE name=?", (table,)) for table in TABLES)


def test_014_migrated_schema_matches_fresh_orm_schema(legacy, monkeypatch, tmp_path):
    path, engine, _ = legacy
    versions = v014(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    migrations.migrate_database(engine)
    fresh = tmp_path / "fresh.sqlite3"
    fresh_engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(fresh_engine)
    fresh_engine.dispose()
    for table in TABLES:
        for pragma in ("table_info", "foreign_key_list", "index_list"):
            assert rows(path, f"PRAGMA {pragma}('{table}')") == rows(fresh, f"PRAGMA {pragma}('{table}')")
