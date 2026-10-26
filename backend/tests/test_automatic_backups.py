"""Automatic local recovery points use the complete, validated backup format."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier, Event, Lock
import time

from fastapi import HTTPException

from backend.services import backups
from backend.tests.test_backups import backup_app, preview  # noqa: F401
from backend.tests.test_pdf_import import pdf_app  # noqa: F401
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


def automatic_files(settings):
    folder = settings.storage_dir / "automatic-backups"
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if backups.AUTOMATIC.fullmatch(path.name))


def test_first_ensure_creates_verified_complete_point_and_second_ensure_reuses_it(backup_app):
    client, _, settings = backup_app
    post(client, payload("A protected local article with durable words.", title="Protected"))

    initial = client.get("/api/backups/automatic")
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "private, no-store"
    assert initial.json() == {
        "database_ok": True,
        "quick_check": "ok",
        "foreign_key_check": "ok",
        "last_success_at": None,
        "last_size_bytes": None,
        "backup_count": 0,
        "next_due_at": None,
        "due": True,
        "last_attempt_at": None,
        "error": None,
        "created": False,
        "retention": 7,
    }

    made = client.post("/api/backups/automatic", json={"force": False})
    assert made.status_code == 200, made.text
    assert made.headers["cache-control"] == "private, no-store"
    result = made.json()
    assert result["created"] is True and result["backup_count"] == 1
    assert result["database_ok"] is True and result["error"] is None
    assert result["last_size_bytes"] > 0 and result["due"] is False
    assert result["last_success_at"] and result["next_due_at"] and result["last_attempt_at"]
    assert len(automatic_files(settings)) == 1
    state = json.loads(
        (settings.storage_dir / "automatic-backups" / backups.AUTOMATIC_STATE).read_text(encoding="utf-8"))
    assert len(state["points"]) == 1
    assert state["points"][0]["filename"] == automatic_files(settings)[0].name
    assert {key: state["points"][0][key] for key in ("sha256", "size")} == backups.digest(automatic_files(settings)[0])

    latest = client.get("/api/backups/automatic/latest")
    assert latest.status_code == 200
    assert latest.headers["content-type"] == "application/zip"
    assert latest.headers["cache-control"] == "private, no-store"
    checked = preview(client, latest.content)
    assert checked.status_code == 200, checked.text
    assert checked.json()["document_count"] == 1

    before_reuse = automatic_files(settings)
    reused = client.post("/api/backups/automatic", json={"force": False})
    assert reused.status_code == 200
    assert reused.json()["created"] is False and reused.json()["backup_count"] == 1
    assert automatic_files(settings) == before_reuse


def test_due_boundary_and_force_create_new_points(backup_app, monkeypatch):
    client, _, settings = backup_app
    clock = [datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc)]
    monkeypatch.setattr(backups, "now", lambda: clock[0])

    first = client.post("/api/backups/automatic", json={"force": False}).json()
    assert first["created"] is True and first["due"] is False
    clock[0] += timedelta(hours=23, minutes=59, seconds=59)
    assert client.post("/api/backups/automatic", json={"force": False}).json()["created"] is False
    assert len(automatic_files(settings)) == 1

    clock[0] += timedelta(seconds=1)
    due = client.post("/api/backups/automatic", json={"force": False}).json()
    assert due["created"] is True and due["backup_count"] == 2

    clock[0] += timedelta(minutes=1)
    forced = client.post("/api/backups/automatic", json={"force": True}).json()
    assert forced["created"] is True and forced["backup_count"] == 3


def test_rotation_keeps_the_seven_newest_only_after_each_valid_point(backup_app, monkeypatch):
    client, _, settings = backup_app
    clock = [datetime(2026, 9, 1, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(backups, "now", lambda: clock[0])
    created_names = []
    for day in range(8):
        clock[0] = datetime(2026, 9, 1, 12, tzinfo=timezone.utc) + timedelta(days=day)
        result = client.post("/api/backups/automatic", json={"force": True})
        assert result.status_code == 200 and result.json()["created"] is True
        created_names.append(backups.latest_automatic_backup().path.name)

    remaining = automatic_files(settings)
    assert len(remaining) == backups.AUTOMATIC_RETENTION == 7
    assert created_names[0] not in {path.name for path in remaining}
    assert {path.name for path in remaining} == set(created_names[1:])
    assert client.get("/api/backups/automatic").json()["backup_count"] == 7


def test_export_or_validation_failure_preserves_previous_point_and_sanitizes_status(backup_app, monkeypatch):
    client, _, settings = backup_app
    assert client.post("/api/backups/automatic", json={"force": False}).json()["created"] is True
    original = automatic_files(settings)
    original_bytes = original[0].read_bytes()

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("Bearer super-secret from C:\\private\\database")

    monkeypatch.setattr(backups, "export_backup", fail_export)
    failed = client.post("/api/backups/automatic", json={"force": True})
    assert failed.status_code == 200
    result = failed.json()
    assert result["created"] is False and result["backup_count"] == 1
    assert result["error"] == backups.AUTOMATIC_ERROR
    assert "secret" not in failed.text and "private" not in failed.text
    assert automatic_files(settings) == original and original[0].read_bytes() == original_bytes
    assert "secret" not in (settings.storage_dir / "automatic-backups" / backups.AUTOMATIC_STATE).read_text(encoding="utf-8")


def test_post_export_validation_failure_never_rotates_a_good_point(backup_app, monkeypatch):
    client, _, settings = backup_app
    assert client.post("/api/backups/automatic", json={"force": False}).json()["created"] is True
    before = automatic_files(settings)
    before_bytes = before[0].read_bytes()

    def reject(*_args, **_kwargs):
        raise HTTPException(422, "simulated invalid archive")

    monkeypatch.setattr(backups, "inspect_archive", reject)
    failed = client.post("/api/backups/automatic", json={"force": True}).json()
    assert failed["created"] is False and failed["backup_count"] == 1
    assert automatic_files(settings) == before and before[0].read_bytes() == before_bytes
    assert not list((settings.storage_dir / "automatic-backups").glob("*.partial"))
    assert not list((settings.storage_dir / "automatic-backups").glob("*.invalid"))


def test_overlapping_forced_requests_share_one_export_attempt(backup_app, monkeypatch):
    client, _, settings = backup_app
    original = backups.export_backup
    barrier, entered, release = Barrier(2), Event(), Event()
    calls, calls_lock = [], Lock()

    def slow_export(*args, **kwargs):
        with calls_lock:
            calls.append(1)
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(backups, "export_backup", slow_export)

    def run(_index):
        barrier.wait(timeout=10)
        response = client.post("/api/backups/automatic", json={"force": True})
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        assert entered.wait(10)
        time.sleep(0.1)
        release.set()
        results = [future.result(timeout=20) for future in futures]

    assert len(calls) == 1
    assert sorted(result["created"] for result in results) == [False, True]
    assert all(result["backup_count"] == 1 for result in results)
    assert len(automatic_files(settings)) == 1


def test_unowned_files_and_tampered_status_are_not_treated_as_recovery_points(backup_app):
    client, _, settings = backup_app
    folder = settings.storage_dir / "automatic-backups"
    folder.mkdir()
    (folder / "manual.zip").write_bytes(b"not-owned")
    (folder / f"lexi-read-auto-not-a-date-{'0' * 32}.zip").write_bytes(b"bad")
    exact_directory = folder / f"lexi-read-auto-20261001T120000000000Z-{'0' * 32}.zip"
    exact_directory.mkdir()
    corrupt_newer = folder / f"lexi-read-auto-20301001T120000000000Z-{'1' * 32}.zip"
    corrupt_newer.write_bytes(b"strictly named but not a backup")
    (folder / backups.AUTOMATIC_STATE).write_text(
        '{"version":1,"attempted_at":"2026-10-01T12:00:00+00:00","error":"Bearer private-token"}',
        encoding="utf-8",
    )

    status = client.get("/api/backups/automatic")
    assert status.status_code == 200
    assert status.json()["backup_count"] == 0 and status.json()["due"] is True
    assert status.json()["error"] == backups.AUTOMATIC_STATE_ERROR
    assert "private-token" not in status.text
    assert client.get("/api/backups/automatic/latest").status_code == 404

    ensured = client.post("/api/backups/automatic", json={"force": False})
    assert ensured.status_code == 200 and ensured.json()["created"] is True
    assert ensured.json()["backup_count"] == 1 and ensured.json()["due"] is False
    latest = client.get("/api/backups/automatic/latest")
    assert latest.status_code == 200 and latest.content != corrupt_newer.read_bytes()
    assert preview(client, latest.content).status_code == 200


def test_changed_verified_archive_is_not_reported_or_downloaded_and_is_replaced_when_due(backup_app):
    client, _, settings = backup_app
    assert client.post("/api/backups/automatic", json={"force": False}).json()["created"] is True
    original = automatic_files(settings)[0]
    damaged = bytearray(original.read_bytes())
    damaged[len(damaged) // 2] ^= 0x01
    original.write_bytes(damaged)  # Preserve size so the SHA-256 check is required.

    status = client.get("/api/backups/automatic")
    assert status.status_code == 200
    assert status.json()["backup_count"] == 0
    assert status.json()["last_success_at"] is None and status.json()["due"] is True
    assert status.json()["error"] == backups.AUTOMATIC_VERIFY_ERROR
    assert client.get("/api/backups/automatic/latest").status_code == 404

    repaired = client.post("/api/backups/automatic", json={"force": False})
    assert repaired.status_code == 200 and repaired.json()["created"] is True
    assert repaired.json()["backup_count"] == 1 and repaired.json()["error"] is None
    latest = client.get("/api/backups/automatic/latest")
    assert latest.status_code == 200
    assert preview(client, latest.content).status_code == 200


def test_corrupt_older_point_is_filtered_while_valid_newest_remains_downloadable(backup_app, monkeypatch):
    client, _, settings = backup_app
    clock = [datetime(2026, 10, 1, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(backups, "now", lambda: clock[0])
    assert client.post("/api/backups/automatic", json={"force": True}).json()["created"] is True
    clock[0] += timedelta(hours=1)
    assert client.post("/api/backups/automatic", json={"force": True}).json()["created"] is True

    state_path = settings.storage_dir / "automatic-backups" / backups.AUTOMATIC_STATE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    older = settings.storage_dir / "automatic-backups" / state["points"][0]["filename"]
    newest = settings.storage_dir / "automatic-backups" / state["points"][1]["filename"]
    damaged = bytearray(older.read_bytes())
    damaged[len(damaged) // 2] ^= 0x01
    older.write_bytes(damaged)

    status = client.get("/api/backups/automatic")
    assert status.status_code == 200
    assert status.json()["backup_count"] == 1
    assert status.json()["last_success_at"] == state["points"][1]["created_at"]
    assert status.json()["due"] is True
    assert status.json()["error"] == backups.AUTOMATIC_VERIFY_ERROR
    latest = client.get("/api/backups/automatic/latest")
    assert latest.status_code == 200 and latest.content == newest.read_bytes()
    assert preview(client, latest.content).status_code == 200

    clock[0] += timedelta(hours=1)
    repaired = client.post("/api/backups/automatic", json={"force": False})
    assert repaired.status_code == 200
    assert repaired.json()["created"] is True and repaired.json()["backup_count"] == 2
    assert repaired.json()["error"] is None
    retained = json.loads(state_path.read_text(encoding="utf-8"))["points"]
    assert older.name not in {point["filename"] for point in retained}


def test_tampered_file_size_is_rejected_before_hashing(backup_app, monkeypatch):
    client, _, settings = backup_app
    assert client.post("/api/backups/automatic", json={"force": False}).json()["created"] is True
    point = automatic_files(settings)[0]
    with point.open("ab") as stream:
        stream.write(b"tampered-size")

    def unexpected_digest(_path):
        raise AssertionError("a size-mismatched recovery point must not be hashed")

    monkeypatch.setattr(backups, "digest", unexpected_digest)
    status = client.get("/api/backups/automatic")
    assert status.status_code == 200
    assert status.json()["backup_count"] == 0
    assert status.json()["last_success_at"] is None and status.json()["due"] is True
    assert status.json()["error"] == backups.AUTOMATIC_VERIFY_ERROR


def test_automatic_request_requires_strict_boolean_and_known_fields(backup_app):
    client, _, settings = backup_app
    for body in ({}, {"force": False}, {"force": True}):
        response = client.post("/api/backups/automatic", json=body)
        assert response.status_code == 200
    count = len(automatic_files(settings))
    for body in ({"force": "true"}, {"force": 1}, {"force": False, "other": True}):
        assert client.post("/api/backups/automatic", json=body).status_code == 422
    assert len(automatic_files(settings)) == count

