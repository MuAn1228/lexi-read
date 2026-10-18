"""Synthetic data only: complete archives and failure-safe local restores."""
import asyncio
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import UploadFile
import pytest
from sqlalchemy import text

from backend import migrations
from backend.models import Document
from backend.services import backups
from backend.services.maintenance import MaintenanceGate
from backend.tests.test_article_library import snapshot
from backend.tests.test_pdf_import import imported, pdf_app  # noqa: F401
from backend.tests.test_migrations import legacy, rows  # noqa: F401
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


@pytest.fixture
def backup_app(pdf_app, monkeypatch):
    client, factory, settings = pdf_app
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.backup_routes.get_settings", lambda: settings)
    migrations.migrate_database(factory.kw["bind"])
    return client, factory, settings


def archive_data(client):
    response = client.get("/api/backups/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    return response.content


def preview(client, data):
    return client.post("/api/backups/preview", files={"file": ("backup.zip", data, "application/zip")})


def restore(client, token):
    return client.post("/api/backups/restore", json={"token": token, "confirm": True})


def populated(client):
    text_doc = post(client, payload("Model examples. Reading is useful.", title="Original"))
    pdf_doc = imported(client)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": pdf_doc["id"]})
    client.put(f"/api/documents/{pdf_doc['id']}", json={"tags": ["Science"], "bookmark_occurrence_id": pdf_doc["words"][-1]["id"]})
    client.delete(f"/api/documents/{pdf_doc['id']}")
    return text_doc, pdf_doc


def all_rows(factory):
    with factory() as session:
        names = [row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
                 if not row[0].startswith("sqlite_")]
        return {name: [dict(row) for row in session.execute(text(f'SELECT * FROM "{name}" ORDER BY 1')).mappings()]
                for name in names}


def comparable(rows):
    result = {table: contents for table, contents in rows.items() if table != "restore_receipts"}
    result["documents"] = [{**row, "image_path": "file" if row["image_path"] else ""} for row in result["documents"]]
    return result


def test_export_preview_restore_every_table_files_trash_and_safety(backup_app):
    client, factory, settings = backup_app
    text_doc, pdf_doc = populated(client)
    before = all_rows(factory)
    original_pdf = Path(before["documents"][1]["image_path"])
    (settings.storage_dir / ".env").write_text("fake-private-config")
    (settings.storage_dir / "server.log").write_text("not data")
    content = archive_data(client)
    with ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        assert set(names[:2]) == {"manifest.json", "data.sqlite3"}
        assert len(names) == 3 and names[2].startswith("assets/") and names[2].endswith(".pdf")
        assert archive.read(names[2]) == original_pdf.read_bytes()
    assert all_rows(factory) == before
    pre = preview(client, content)
    assert pre.status_code == 200, pre.text
    info = pre.json()
    assert info["document_count"] == 2 and info["file_count"] == 1
    assert info["vocabulary_count"] == len(before["vocabulary"]) and info["event_count"] == len(before["learning_events"])
    assert all_rows(factory) == before
    post(client, payload("Changed material with new words.", title="Later"))
    client.put(f"/api/documents/{text_doc['id']}", json={"title": "Changed title"})
    changed = all_rows(factory)
    response = restore(client, info["token"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["restored"] is True
    assert comparable(all_rows(factory)) == comparable(before)
    with factory() as session:
        doc = session.get(Document, pdf_doc["id"])
        restored_pdf = Path(doc.image_path)
        assert restored_pdf != original_pdf and restored_pdf.read_bytes() == original_pdf.read_bytes()
        assert doc.deleted_at is not None and restored_pdf.parent.parent == settings.storage_dir / "restored"
    assert original_pdf.is_file()
    safety = client.get(result["safety_backup_url"])
    assert safety.status_code == 200
    pre_safety = preview(client, safety.content)
    assert pre_safety.status_code == 200, pre_safety.text
    assert pre_safety.json()["document_count"] == 3
    assert restore(client, pre_safety.json()["token"]).status_code == 200
    assert comparable(all_rows(factory)) == comparable(changed)
    assert not list((settings.storage_dir / "backup-downloads").glob("*.zip"))


def test_completed_token_survives_lost_response_process_restart_and_later_restore(backup_app, monkeypatch):
    client, factory, settings = backup_app
    populated(client)
    content = archive_data(client)
    token = preview(client, content).json()["token"]
    post(client, payload("Later changes."))
    result = restore(client, token)
    assert result.status_code == 200
    post(client, payload("Must survive receipt replay."))
    before = all_rows(factory)
    paths = list((settings.storage_dir / "safety-backups").glob("*.zip"))
    assert restore(client, token).json() == result.json()
    assert all_rows(factory) == before and list((settings.storage_dir / "safety-backups").glob("*.zip")) == paths
    backups._result_file(token).unlink()
    assert restore(client, token).json() == result.json()  # DB receipt fallback.
    assert all_rows(factory) == before
    # Restoring a snapshot created before this receipt still keeps it.
    second = preview(client, content).json()["token"]
    assert restore(client, second).status_code == 200
    post(client, payload("Keep this last article."))
    before = all_rows(factory)
    assert restore(client, token).json() == result.json()
    assert all_rows(factory) == before


@pytest.mark.parametrize("kind", ["broken", "traversal", "absolute", "backslash", "duplicate", "symlink", "unknown", "hash", "missing"])
def test_unsafe_or_corrupt_archives_are_rejected_before_any_database_write(backup_app, kind):
    client, factory, settings = backup_app
    populated(client)
    before = all_rows(factory)
    content = archive_data(client)
    with ZipFile(BytesIO(content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    target = BytesIO()
    if kind == "broken":
        content = b"not a zip"
    else:
        with ZipFile(target, "w", ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                if kind == "missing" and name == "data.sqlite3":
                    continue
                if kind == "hash" and name == "data.sqlite3":
                    data += b"tamper"
                archive.writestr(name, data)
            if kind == "duplicate":
                archive.writestr("data.sqlite3", entries["data.sqlite3"])
            elif kind == "symlink":
                entry = ZipInfo(f"assets/{uuid4().hex}.pdf")
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "../../outside")
            elif kind in {"traversal", "absolute", "backslash", "unknown"}:
                name = {"traversal": "../outside", "absolute": "/outside", "backslash": "..\\outside", "unknown": ".env"}[kind]
                archive.writestr(name, "forbidden")
        content = target.getvalue()
    response = preview(client, content)
    assert response.status_code == 422, response.text
    assert all_rows(factory) == before
    assert not list((settings.storage_dir / "backup-staging").glob("*"))
    assert not list((settings.storage_dir / "restored").glob("*"))


@pytest.mark.parametrize("kind", ["unknown-table", "unknown-version", "bad-fk", "bad-json", "trigger", "missing-column", "missing-migration-time"])
def test_valid_zip_hashes_cannot_hide_invalid_database(backup_app, tmp_path, kind):
    client, factory, _ = backup_app
    populated(client)
    before = all_rows(factory)
    with ZipFile(BytesIO(archive_data(client))) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    path = tmp_path / "tamper.sqlite3"
    path.write_bytes(entries["data.sqlite3"])
    with closing(sqlite3.connect(path)) as connection:
        statements = {
            "unknown-table": "CREATE TABLE evil(id INTEGER)",
            "unknown-version": "INSERT INTO schema_migrations VALUES ('999_future','2026-01-01')",
            "bad-fk": "UPDATE word_occurrences SET document_id=99999",
            "bad-json": "UPDATE documents SET tags='invalid json'",
            "trigger": "CREATE TRIGGER evil AFTER INSERT ON users BEGIN SELECT 1; END",
            "missing-column": "ALTER TABLE documents DROP COLUMN pages",
            "missing-migration-time": "ALTER TABLE schema_migrations DROP COLUMN applied_at",
        }
        connection.execute(statements[kind])
        connection.commit()
    manifest = json.loads(entries["manifest.json"])
    manifest["files"]["data.sqlite3"] = backups.digest(path)
    entries.update({"data.sqlite3": path.read_bytes(), "manifest.json": json.dumps(manifest).encode()})
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    response = preview(client, stream.getvalue())
    assert response.status_code == 422, response.text
    assert all_rows(factory) == before


@pytest.mark.parametrize("failure", ["safety", "rows", "copy"])
def test_failed_apply_preserves_every_original_row_and_file(backup_app, monkeypatch, failure):
    client, factory, settings = backup_app
    populated(client)
    token = preview(client, archive_data(client)).json()["token"]
    post(client, payload("Changes after snapshot."))
    before = all_rows(factory)
    original = {str(path): path.read_bytes() for path in settings.storage_dir.rglob("*.pdf")}
    def fail(*args, **kwargs):
        raise RuntimeError("simulated backup failure")
    if failure == "safety":
        monkeypatch.setattr(backups, "export_backup", fail)
    elif failure == "rows":
        # Fail after DELETE statements and some INSERTs in the real transaction.
        engine = factory.kw["bind"]
        raw = engine.raw_connection()
        raw.driver_connection.execute("CREATE TRIGGER block_restore BEFORE INSERT ON vocabulary BEGIN SELECT RAISE(ABORT, 'restore blocked'); END")
        raw.driver_connection.commit()
        raw.close()
    else:
        monkeypatch.setattr(backups.shutil, "copyfile", fail)
    if failure == "rows":
        # The safety exporter deliberately rejects unknown triggers. Inject after that safe archive is complete.
        raw = factory.kw["bind"].raw_connection()
        raw.driver_connection.execute("DROP TRIGGER block_restore")
        raw.driver_connection.commit()
        raw.close()
        original_replace = backups._replace_data
        def blocked(engine, *args):
            raw = engine.raw_connection()
            raw.driver_connection.execute("CREATE TRIGGER block_restore BEFORE INSERT ON vocabulary BEGIN SELECT RAISE(ABORT, 'restore blocked'); END")
            raw.driver_connection.commit()
            raw.close()
            try:
                return original_replace(engine, *args)
            finally:
                raw = engine.raw_connection()
                raw.driver_connection.execute("DROP TRIGGER block_restore")
                raw.driver_connection.commit()
                raw.close()
        monkeypatch.setattr(backups, "_replace_data", blocked)
    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        backups.restore_backup(factory.kw["bind"], token)
    assert all_rows(factory) == before
    assert {str(path): path.read_bytes() for path in settings.storage_dir.rglob("*.pdf")} == original
    assert not list((settings.storage_dir / "restored").glob("*"))


def test_missing_asset_or_outside_storage_cannot_be_exported(backup_app, tmp_path):
    client, factory, _ = backup_app
    doc = imported(client)
    outside = tmp_path.parent / f"outside-{uuid4().hex}.pdf"
    outside.write_bytes(b"outside")
    try:
        with factory() as session:
            session.get(Document, doc["id"]).image_path = str(outside)
            session.commit()
        assert client.get("/api/backups/export").status_code == 422
    finally:
        outside.unlink()


def test_preview_expiry_and_archive_change_reject_apply(backup_app):
    client, factory, settings = backup_app
    populated(client)
    content = archive_data(client)
    before = all_rows(factory)
    token = preview(client, content).json()["token"]
    folder = settings.storage_dir / "backup-staging" / token
    metadata = json.loads((folder / "preview.json").read_text())
    metadata["preview"]["expires_at"] = "2020-01-01T00:00:00+00:00"
    (folder / "preview.json").write_text(json.dumps(metadata))
    assert restore(client, token).status_code == 410
    other = preview(client, content).json()["token"]
    (settings.storage_dir / "backup-staging" / other / "archive.zip").write_bytes(b"changed")
    assert restore(client, other).status_code == 422
    assert all_rows(factory) == before


@pytest.mark.parametrize("body", [{}, {"token": "invalid", "confirm": True}, {"token": "0" * 32},
                                     {"token": "0" * 32, "confirm": False}, {"token": "0" * 32, "confirm": "true"},
                                     {"token": "0" * 32, "confirm": 1}])
def test_restore_requires_preview_token_and_explicit_confirmation(backup_app, body):
    client, factory, _ = backup_app
    before = all_rows(factory)
    assert client.post("/api/backups/restore", json=body).status_code == 422
    assert all_rows(factory) == before


def test_public_mode_has_no_backup_access(backup_app):
    client, factory, settings = backup_app
    settings.app_mode = "public"
    before = all_rows(factory)
    assert client.get("/api/backups/export").status_code == 404
    assert preview(client, b"invalid").status_code == 404
    assert restore(client, "0" * 32).status_code == 404
    assert client.get(f"/api/backups/safety/lexi-read-safety-{'0' * 32}.zip").status_code == 404
    assert all_rows(factory) == before


def test_maintenance_gate_drains_existing_work_and_blocks_new_requests():
    async def run():
        gate, events = MaintenanceGate(), []
        existing_started, finish_existing = asyncio.Event(), asyncio.Event()
        restore_started, finish_restore = asyncio.Event(), asyncio.Event()
        async def existing():
            async with gate.access():
                events.append("existing")
                existing_started.set()
                await finish_existing.wait()
                events.append("existing-finished")
        async def applying():
            async with gate.access(True):
                events.append("restore")
                restore_started.set()
                await finish_restore.wait()
                events.append("restore-finished")
        async def following():
            async with gate.access():
                events.append("following")
        first = asyncio.create_task(existing())
        await existing_started.wait()
        second = asyncio.create_task(applying())
        await asyncio.sleep(0)
        third = asyncio.create_task(following())
        await asyncio.sleep(0)
        assert events == ["existing"]
        finish_existing.set()
        await restore_started.wait()
        assert events == ["existing", "existing-finished", "restore"]
        finish_restore.set()
        await asyncio.gather(first, second, third)
        assert events == ["existing", "existing-finished", "restore", "restore-finished", "following"]
    asyncio.run(run())


def test_concurrent_restore_requests_share_one_commit_and_safety_backup(backup_app):
    client, factory, settings = backup_app
    populated(client)
    token = preview(client, archive_data(client)).json()["token"]
    post(client, payload("New article removed once."))
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=10)
        response = restore(client, token)
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == results[1]
    assert len(list((settings.storage_dir / "safety-backups").glob("*.zip"))) == 1
    with factory() as session:
        assert session.execute(text("SELECT count(*) FROM restore_receipts")).scalar() == 1


def test_filesystem_receipt_write_failure_is_recovered_from_atomic_db_receipt(backup_app, monkeypatch):
    client, factory, _ = backup_app
    populated(client)
    token = preview(client, archive_data(client)).json()["token"]
    original_save = backups._save_result
    def fail(*args):
        raise OSError("receipt disk unavailable")
    monkeypatch.setattr(backups, "_save_result", fail)
    with pytest.raises(OSError, match="receipt disk unavailable"):
        backups.restore_backup(factory.kw["bind"], token)
    post(client, payload("Must survive retry after lost success."))
    before = all_rows(factory)
    monkeypatch.setattr(backups, "_save_result", original_save)
    assert restore(client, token).status_code == 200
    assert all_rows(factory) == before


@pytest.mark.parametrize("limit", ["archive", "expanded", "entries"])
def test_bounded_preview_rejects_size_or_entry_limits(backup_app, monkeypatch, limit):
    client, factory, _ = backup_app
    content = archive_data(client)
    before = all_rows(factory)
    monkeypatch.setattr(backups, {"archive": "MAX_ARCHIVE_BYTES", "expanded": "MAX_EXPANDED_BYTES", "entries": "MAX_ENTRIES"}[limit], 1)
    assert preview(client, content).status_code == 413
    assert all_rows(factory) == before


def test_011_upgrade_only_adds_receipts_and_preserves_backup_and_repeat(legacy, monkeypatch):
    path, engine, connection = legacy
    versions = migrations.MIGRATIONS[:11]
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:10])
    migrations.migrate_database(engine)
    names = [row["name"] for row in rows(path, "SELECT name FROM sqlite_master WHERE type='table'")
             if row["name"] not in {"schema_migrations", "sqlite_sequence"}]
    before = {name: rows(path, f"SELECT * FROM {name} ORDER BY 1") for name in names}
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    backup = migrations.migrate_database(engine)
    assert backup and "before-011_restore_receipts" in backup.name
    for name, contents in before.items():
        assert rows(path, f"SELECT * FROM {name} ORDER BY 1") == contents
        assert rows(backup, f"SELECT * FROM {name} ORDER BY 1") == contents
    assert rows(path, "SELECT * FROM restore_receipts") == []
    assert migrations.migrate_database(engine) is None
    connection.execute("INSERT INTO restore_receipts VALUES (?,?,?)", ("0" * 32, '{"restored":true}', "2026-01-01"))
    connection.execute("DELETE FROM schema_migrations WHERE version=?", (versions[-1][0],))
    connection.commit()
    receipts = rows(path, "SELECT * FROM restore_receipts")
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT * FROM restore_receipts") == receipts


def test_011_failed_upgrade_rolls_back_and_can_retry(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS[:11]
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:10])
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM documents")
    def fail(connection, tables):
        migrations._create_restore_receipts(connection, tables)
        raise RuntimeError("interrupted receipts upgrade")
    monkeypatch.setattr(migrations, "MIGRATIONS", [*versions[:10], (versions[-1][0], fail)])
    with pytest.raises(RuntimeError, match="interrupted receipts upgrade"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT * FROM documents") == before
    assert rows(path, "SELECT name FROM sqlite_master WHERE name='restore_receipts'") == []
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 10
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    assert migrations.migrate_database(engine) is not None


def test_cancelled_waiting_restore_releases_readers():
    async def run():
        gate = MaintenanceGate()
        async with gate.access():
            async def restoring():
                async with gate.access(True):
                    pytest.fail("The existing reader still holds access")
            task = asyncio.create_task(restoring())
            await asyncio.sleep(0)
            assert gate.waiting == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate.waiting == 0
        async with gate.access():
            assert gate.active == 1
    asyncio.run(run())
