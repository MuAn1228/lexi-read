"""The vocabulary tools upgrade must preserve installed learning data and backups."""
from contextlib import closing
from io import BytesIO
import json
import sqlite3
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend import migrations
from backend.services import backups
from backend.tests.test_backups import archive_data, backup_app, preview, restore  # noqa: F401
from backend.tests.test_migrations import legacy  # noqa: F401
from backend.tests.test_pdf_import import pdf_app  # noqa: F401
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


def rows_by_table(path, tables):
    with closing(sqlite3.connect(path)) as db:
        return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in tables}


def test_013_preserves_all_existing_tables_and_is_repeatable(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, 'MIGRATIONS', versions[:-1])
    migrations.migrate_database(engine)
    with closing(sqlite3.connect(path)) as db:
        tables = backups.table_names(db) - {'schema_migrations'}
    before = rows_by_table(path, tables)
    monkeypatch.setattr(migrations, 'MIGRATIONS', versions)
    safety = migrations.migrate_database(engine)
    assert safety and safety.is_file() and rows_by_table(path, tables) == before
    with closing(sqlite3.connect(path)) as db:
        assert backups.VOCABULARY_TOOL_TABLES <= backups.table_names(db)
        assert all(db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
                   for table in backups.VOCABULARY_TOOL_TABLES)
    assert migrations.migrate_database(engine) is None
    assert rows_by_table(path, tables) == before


def test_013_ddl_failure_rolls_back_without_marking_upgrade_complete(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, 'MIGRATIONS', versions[:-1])
    migrations.migrate_database(engine)

    def interrupted(connection, tables):
        migrations._create_vocabulary_tools(connection, tables)
        raise RuntimeError('Synthetic migration interruption')

    monkeypatch.setattr(migrations, 'MIGRATIONS', [*versions[:-1], (versions[-1][0], interrupted)])
    with pytest.raises(RuntimeError):
        migrations.migrate_database(engine)
    with closing(sqlite3.connect(path)) as db:
        assert not backups.VOCABULARY_TOOL_TABLES & backups.table_names(db)
        assert db.execute('SELECT count(*) FROM schema_migrations WHERE version=?', (versions[-1][0],)).fetchone()[0] == 0
    monkeypatch.setattr(migrations, 'MIGRATIONS', versions)
    migrations.migrate_database(engine)


def marked_fixture(client):
    post(client, payload('A model learns. Readers learn.', title='Vocabulary tools'))
    words = client.get('/api/vocabulary').json()
    group = client.post('/api/vocabulary-groups', json={'name': '阅读生词'}).json()
    ids = [word['id'] for word in words[:2]]
    assert client.post(f"/api/vocabulary-groups/{group['id']}/members",
                       json={'vocabulary_ids': ids, 'action': 'add'}).status_code == 200
    result = client.post('/api/vocabulary/batch', json={
        'request_id': str(uuid4()), 'status': 'UNKNOWN',
        'items': [{'id': word['id'], 'updated_at': word['updated_at']} for word in words[:2]],
    })
    assert result.status_code == 200, result.text
    return group, result.json()


def test_complete_backup_restores_groups_members_and_reversible_operation(backup_app):
    client, _, _ = backup_app
    group, operation = marked_fixture(client)
    original = archive_data(client)
    receipt = preview(client, original)
    assert receipt.status_code == 200, receipt.text
    assert client.delete(f"/api/vocabulary-groups/{group['id']}").status_code == 204
    assert client.post(f"/api/vocabulary/batch/{operation['operation_id']}/undo").status_code == 200
    assert restore(client, receipt.json()['token']).status_code == 200
    groups = client.get('/api/vocabulary-groups').json()['groups']
    assert len(groups) == 1 and groups[0]['name'] == group['name'] and len(groups[0]['vocabulary_ids']) == 2
    # A restored undo receipt remains usable and returns original unassessed state.
    undone = client.post(f"/api/vocabulary/batch/{operation['operation_id']}/undo")
    assert undone.status_code == 200, undone.text
    assert all(item['status'] is None for item in undone.json()['items'])


def test_v012_backup_restores_with_empty_new_tables_and_current_version(backup_app, tmp_path):
    client, _, _ = backup_app
    post(client, payload('Old reading data.', title='Old archive'))
    with ZipFile(BytesIO(archive_data(client))) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    path = tmp_path / 'previous.sqlite3'
    path.write_bytes(entries['data.sqlite3'])
    with closing(sqlite3.connect(path)) as db:
        for table in ('vocabulary_group_members', 'vocabulary_groups', 'vocabulary_operations'):
            db.execute(f'DROP TABLE "{table}"')
        db.execute("DELETE FROM schema_migrations WHERE version='013_vocabulary_tools'")
        db.commit()
    manifest = json.loads(entries['manifest.json'])
    manifest['files']['data.sqlite3'] = backups.digest(path)
    entries.update({'data.sqlite3': path.read_bytes(), 'manifest.json': json.dumps(manifest).encode()})
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    marked_fixture(client)
    before = preview(client, output.getvalue())
    assert before.status_code == 200, before.text
    assert restore(client, before.json()['token']).status_code == 200
    assert client.get('/api/vocabulary-groups').json() == {'groups': []}
    assert [doc['title'] for doc in client.get('/api/documents').json()] == ['Old archive']
    # Export the restored result as a valid current-version archive, without a restart.
    fresh = preview(client, archive_data(client))
    assert fresh.status_code == 200, fresh.text


def test_deeply_nested_operation_json_is_a_validation_error_not_a_server_error(backup_app, tmp_path):
    client, _, _ = backup_app
    marked_fixture(client)
    with ZipFile(BytesIO(archive_data(client))) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    path = tmp_path / 'nested.sqlite3'
    path.write_bytes(entries['data.sqlite3'])
    with closing(sqlite3.connect(path)) as db:
        db.execute('UPDATE vocabulary_operations SET snapshots=?', ('[' * 1500 + '0' + ']' * 1500,))
        db.commit()
    manifest = json.loads(entries['manifest.json'])
    manifest['files']['data.sqlite3'] = backups.digest(path)
    entries.update({'data.sqlite3': path.read_bytes(), 'manifest.json': json.dumps(manifest).encode()})
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    response = preview(client, output.getvalue())
    assert response.status_code == 422
    assert len(client.get('/api/vocabulary-groups').json()['groups']) == 1
