"""Complete backups preserve sense state and reject unsafe sense records."""
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import text

from backend.services import backups
from backend.tests.test_backups import (all_rows, archive_data, backup_app, comparable,
                                        preview, restore)  # noqa: F401
from backend.tests.test_pdf_import import pdf_app  # noqa: F401
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


def seed_sense(client, factory):
    document = post(client, payload("The bank approved the account.", title="Sense backup"))
    with factory() as session:
        vocabulary_id = session.execute(text(
            "SELECT id FROM vocabulary WHERE user_id=1 AND lemma='bank'"
        )).scalar_one()
        session.execute(text(
            "INSERT INTO vocabulary_senses "
            "(user_id,vocabulary_id,sense_key,source,source_revision,headword,source_ordinal,"
            "part_of_speech,meaning,status,mastery_score,due_at,known_streak,created_at,updated_at) "
            "VALUES (1,:vocabulary_id,:sense_key,'ecdict',:revision,'bank',0,'n.',"
            "'银行；金融机构','UNKNOWN',0.25,'2026-11-01 08:00:00',1,"
            "'2026-10-27 08:00:00','2026-10-27 08:00:00')"
        ), {"vocabulary_id": vocabulary_id, "sense_key": "a" * 64, "revision": "b" * 64})
        sense_id = session.execute(text("SELECT id FROM vocabulary_senses")).scalar_one()
        request_id = str(uuid4())
        session.execute(text(
            "INSERT INTO sense_review_receipts "
            "(user_id,request_id,sense_id,status,document_id,response_ms,plan_minutes,created_at) "
            "VALUES (1,:request_id,:sense_id,'UNKNOWN',:document_id,1250,20,'2026-10-27 08:00:00')"
        ), {"request_id": request_id, "sense_id": sense_id, "document_id": document["id"]})
        session.commit()
    return document


def tamper_archive(content: bytes, path: Path, statements: list[str]) -> bytes:
    with ZipFile(BytesIO(content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    path.write_bytes(entries["data.sqlite3"])
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    manifest = json.loads(entries["manifest.json"])
    manifest["files"]["data.sqlite3"] = backups.digest(path)
    entries.update({"data.sqlite3": path.read_bytes(),
                    "manifest.json": json.dumps(manifest).encode()})
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_current_backup_round_trip_preserves_senses_and_receipts(backup_app):
    client, factory, _ = backup_app
    seed_sense(client, factory)
    before = all_rows(factory)
    content = archive_data(client)
    inspected = preview(client, content)
    assert inspected.status_code == 200, inspected.text
    with factory() as session:
        session.execute(text("DELETE FROM sense_review_receipts"))
        session.execute(text("DELETE FROM vocabulary_senses"))
        session.commit()
    restored = restore(client, inspected.json()["token"])
    assert restored.status_code == 200, restored.text
    assert comparable(all_rows(factory)) == comparable(before)


@pytest.mark.parametrize("kind,statements", [
    ("sense-owner", ["UPDATE vocabulary_senses SET user_id=2"]),
    ("receipt-sense-owner", ["UPDATE sense_review_receipts SET user_id=2"]),
    ("receipt-document-owner", ["UPDATE documents SET user_id=2 WHERE id=(SELECT document_id FROM sense_review_receipts)"]),
    ("sense-key", ["UPDATE vocabulary_senses SET sense_key='ABC'"]),
    ("source", ["UPDATE vocabulary_senses SET source='untrusted'"]),
    ("source-revision", ["UPDATE vocabulary_senses SET source_revision='ABC'"]),
    ("headword", ["UPDATE vocabulary_senses SET headword=' '"]),
    ("ordinal", ["UPDATE vocabulary_senses SET source_ordinal=1001"]),
    ("meaning", ["UPDATE vocabulary_senses SET meaning=' '"]),
    ("sense-status", ["UPDATE vocabulary_senses SET status='MAYBE'"]),
    ("mastery", ["UPDATE vocabulary_senses SET mastery_score=2"]),
    ("due", ["UPDATE vocabulary_senses SET due_at='not-a-date'"]),
    ("streak", ["UPDATE vocabulary_senses SET known_streak=6"]),
    ("request-id", ["UPDATE sense_review_receipts SET request_id='not-a-uuid'"]),
    ("receipt-status", ["UPDATE sense_review_receipts SET status='MAYBE'"]),
    ("response", ["UPDATE sense_review_receipts SET response_ms=499"]),
    ("plan", ["UPDATE sense_review_receipts SET plan_minutes=15"]),
])
def test_preview_rejects_invalid_or_cross_owner_sense_data_without_writes(
        backup_app, tmp_path, kind, statements):
    client, factory, _ = backup_app
    seed_sense(client, factory)
    before = all_rows(factory)
    content = tamper_archive(archive_data(client), tmp_path / f"{kind}.sqlite3", statements)
    response = preview(client, content)
    assert response.status_code == 422, response.text
    assert all_rows(factory) == before
