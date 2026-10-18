"""Local complete backups with bounded validation and atomic SQLite restores."""
from datetime import datetime, timedelta, timezone
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from fastapi import HTTPException, UploadFile
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.engine import Engine

from backend.config import get_settings
from backend.migrations import MIGRATIONS
from backend.models import Base


MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_ENTRIES = 10_000
TOKEN = re.compile(r"^[a-f0-9]{32}$")
ASSET = re.compile(r"^assets/[a-f0-9]{32}\.(png|pdf)$")
SAFETY = re.compile(r"^lexi-read-safety-[a-f0-9]{32}\.zip$")
TTL = timedelta(minutes=30)


def now():
    return datetime.now(timezone.utc)


def invalid(message="备份文件无效或不完整，请选择由当前版本导出的完整备份。"):
    return HTTPException(422, message)


def root() -> Path:
    folder = get_settings().storage_dir
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def within(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder.resolve())


def digest(path: Path) -> dict:
    value, size = sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            value.update(chunk)
    return {"sha256": value.hexdigest(), "size": size}


@contextmanager
def read_database(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def table_names(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")}


def validate_database(connection: sqlite3.Connection):
    """Never execute incoming DDL. Match the known tables and validate data first."""
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise invalid("备份数据库完整性检查失败。")
    if list(connection.execute("PRAGMA foreign_key_check")):
        raise invalid("备份数据库的关联数据不完整。")
    if list(connection.execute("SELECT name FROM sqlite_master WHERE type IN ('trigger','view')")):
        raise invalid("备份数据库包含不支持的结构。")
    expected = set(Base.metadata.tables) | {"schema_migrations"}
    if table_names(connection) != expected:
        raise invalid("备份的数据结构与当前版本不一致，请使用相同版本的完整备份。")
    if {row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)")} != {"version", "applied_at"}:
        raise invalid("备份的版本记录结构与当前版本不一致。")
    versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if versions != sorted(version for version, _ in MIGRATIONS):
        raise invalid("备份版本与当前版本不一致，请使用相同版本的完整备份。")
    for table in Base.metadata.sorted_tables:
        columns = list(connection.execute(f'PRAGMA table_info("{table.name}")'))
        if {row[1] for row in columns} != {column.name for column in table.columns}:
            raise invalid("备份的数据列与当前版本不一致。")
        actual_fks = {(row[2], row[3], row[4]) for row in connection.execute(f'PRAGMA foreign_key_list("{table.name}")')}
        expected_fks = {(key.column.table.name, key.parent.name, key.column.name) for key in table.foreign_keys}
        if actual_fks != expected_fks:
            raise invalid("备份数据库的关联结构与当前版本不一致。")
        for row in connection.execute(f'SELECT * FROM "{table.name}"'):
            for column in table.columns:
                value = row[column.name]
                if value is None:
                    if not column.nullable:
                        raise invalid()
                    continue
                if isinstance(column.type, JSON):
                    try:
                        json.loads(value)
                    except (ValueError, TypeError):
                        raise invalid() from None
                elif isinstance(column.type, Boolean) and value not in (0, 1):
                    raise invalid()
                elif isinstance(column.type, DateTime):
                    try:
                        datetime.fromisoformat(value)
                    except (ValueError, TypeError):
                        raise invalid() from None
                elif isinstance(column.type, Integer) and not isinstance(value, int):
                    raise invalid()
                elif isinstance(column.type, Float) and (not isinstance(value, (float, int)) or not math.isfinite(value)):
                    raise invalid()
                elif isinstance(column.type, (String, Text)) and not isinstance(value, str):
                    raise invalid()
    if connection.execute("SELECT id FROM users WHERE id=1").fetchone() is None:
        raise invalid("备份缺少本机用户的数据。")
    documents = {}
    for document in connection.execute("SELECT * FROM documents"):
        tags, pages = json.loads(document["tags"]), json.loads(document["pages"])
        if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(tag, str) or not 1 <= len(tag) <= 20 for tag in tags):
            raise invalid("备份中的文章标签无效。")
        if not isinstance(pages, list) or (document["source"] == "pdf" and not 1 <= len(pages) <= 100):
            raise invalid("备份中的 PDF 页记录无效。")
        end = -2
        for number, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or set(page) != {"number", "text_start", "text_end"}
                    or any(type(value) is not int for value in page.values()) or page["number"] != number
                    or page["text_start"] != end + 2
                    or not 0 <= page["text_start"] <= page["text_end"] <= len(document["ocr_text"])):
                raise invalid("备份中的 PDF 页位置无效。")
            end = page["text_end"]
        if pages and end != len(document["ocr_text"]):
            raise invalid("备份中的 PDF 页位置不完整。")
        documents[document["id"]] = document
    for word in connection.execute("SELECT document_id,word,text_start,text_end,bbox FROM word_occurrences"):
        document = documents[word["document_id"]]
        if document["source"] in {"text", "pdf"}:
            start, end = word["text_start"], word["text_end"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document["ocr_text"])
                    or document["ocr_text"][start:end] != word["word"]):
                raise invalid("备份中的单词位置无效。")
        bbox = json.loads(word["bbox"])
        if not isinstance(bbox, list) or len(bbox) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
            raise invalid("备份中的单词坐标无效。")
    for table, column in (("learning_events", "details"), ("restore_receipts", "result")):
        if any(not isinstance(json.loads(row[0]), dict) for row in connection.execute(f'SELECT "{column}" FROM "{table}"')):
            raise invalid("备份中的学习记录格式无效。")


def export_backup(engine: Engine, destination: Path) -> Path:
    """SQLite online backup includes committed WAL data; assets are immutable."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".backup-work-", dir=root()) as temporary:
        folder = Path(temporary)
        database = folder / "data.sqlite3"
        raw = engine.raw_connection()
        try:
            with closing(sqlite3.connect(database)) as snapshot:
                raw.driver_connection.backup(snapshot)
        finally:
            raw.close()
        with read_database(database) as connection:
            validate_database(connection)
            documents = list(connection.execute("SELECT id,source,image_path FROM documents ORDER BY id"))
        files, mapping, assets, sources = {"data.sqlite3": digest(database)}, {}, {}, {}
        for document in documents:
            if document["source"] == "text":
                if document["image_path"]:
                    raise invalid("文章文件记录无效，无法生成完整备份。")
                mapping[str(document["id"])] = None
                continue
            if document["source"] not in {"upload", "demo", "pdf"}:
                raise invalid("存在不支持的文章来源，无法生成完整备份。")
            path = Path(document["image_path"])
            extension = ".pdf" if document["source"] == "pdf" else ".png"
            if not within(path, root()) or not path.is_file() or path.suffix.lower() != extension:
                raise invalid("有文章原文件缺失或路径无效，请先修复原文件再备份。")
            resolved = path.resolve()
            if resolved not in sources:
                name = f"assets/{uuid4().hex}{extension}"
                sources[resolved] = name
                assets[name] = resolved
                files[name] = digest(resolved)
            mapping[str(document["id"])] = sources[resolved]
        manifest = {"format": "lexi-read-complete", "version": 1, "created_at": now().isoformat(),
                    "files": files, "documents": mapping}
        if sum(value["size"] for value in files.values()) > MAX_EXPANDED_BYTES or len(files) + 1 > MAX_ENTRIES:
            raise HTTPException(413, "完整备份内容超过当前支持的 500 MB 或文件数量限制。")
        partial = destination.with_suffix(".partial")
        try:
            with ZipFile(partial, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True))
                archive.write(database, "data.sqlite3")
                for name, path in assets.items():
                    archive.write(path, name)
            if partial.stat().st_size > MAX_ARCHIVE_BYTES:
                raise HTTPException(413, "压缩后的完整备份超过当前支持的 250 MB。")
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    return destination


def inspect_archive(archive_path: Path, folder: Path) -> dict:
    """Strict allowlist extraction without extractall or incoming filesystem paths."""
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise HTTPException(413, "备份压缩包不能超过 250 MB。")
    try:
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise HTTPException(413, "备份展开后不能超过 500 MB 或 10,000 个文件。")
            if len(set(names)) != len(names) or "manifest.json" not in names or "data.sqlite3" not in names:
                raise invalid()
            for entry in entries:
                mode = entry.external_attr >> 16
                if (entry.filename not in {"manifest.json", "data.sqlite3"} and not ASSET.fullmatch(entry.filename)
                        or entry.orig_filename != entry.filename or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                        or entry.is_dir() or entry.flag_bits & 1
                        or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}):
                    raise invalid("备份包含不允许的路径或文件类型。")
            if archive.getinfo("manifest.json").file_size > 1024 * 1024:
                raise invalid()
            manifest = json.loads(archive.read("manifest.json"))
            if (not isinstance(manifest, dict) or manifest.get("format") != "lexi-read-complete"
                    or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict)
                    or not isinstance(manifest.get("documents"), dict)):
                raise invalid()
            datetime.fromisoformat(manifest["created_at"])
            if set(manifest["files"]) != set(names) - {"manifest.json"}:
                raise invalid()
            expanded = 0
            folder.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                if entry.filename == "manifest.json":
                    continue
                path = folder / entry.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                value, size = sha256(), 0
                with archive.open(entry) as source, path.open("wb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        expanded += len(chunk)
                        if expanded > MAX_EXPANDED_BYTES:
                            raise HTTPException(413, "备份展开后不能超过 500 MB。")
                        value.update(chunk)
                        target.write(chunk)
                if manifest["files"][entry.filename] != {"sha256": value.hexdigest(), "size": size}:
                    raise invalid("备份文件校验失败，文件可能已损坏。")
        with read_database(folder / "data.sqlite3") as connection:
            validate_database(connection)
            documents = list(connection.execute("SELECT id,source FROM documents"))
            mapping = manifest["documents"]
            if set(mapping) != {str(document["id"]) for document in documents}:
                raise invalid()
            used = set()
            for document in documents:
                name = mapping[str(document["id"])]
                if document["source"] == "text":
                    if name is not None:
                        raise invalid()
                elif document["source"] in {"upload", "demo", "pdf"}:
                    extension = ".pdf" if document["source"] == "pdf" else ".png"
                    if not isinstance(name, str) or not ASSET.fullmatch(name) or not name.endswith(extension):
                        raise invalid()
                    used.add(name)
                else:
                    raise invalid()
            if used != set(manifest["files"]) - {"data.sqlite3"}:
                raise invalid()
            return {"created_at": manifest["created_at"], "document_count": len(documents),
                    "vocabulary_count": connection.execute("SELECT count(*) FROM vocabulary").fetchone()[0],
                    "event_count": connection.execute("SELECT count(*) FROM learning_events").fetchone()[0],
                    "file_count": len(used), "manifest": manifest}
    except HTTPException:
        raise
    except (BadZipFile, OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        raise invalid() from exc


def preview_backup(upload: UploadFile) -> dict:
    staging = root() / "backup-staging"
    if staging.exists():
        for old in staging.iterdir():
            if (TOKEN.fullmatch(old.name) and not old.is_symlink() and old.is_dir() and within(old, staging)
                    and now().timestamp() - old.stat().st_mtime > TTL.total_seconds()):
                shutil.rmtree(old)
    token = uuid4().hex
    folder = root() / "backup-staging" / token
    folder.mkdir(parents=True)
    try:
        size = 0
        archive = folder / "archive.zip"
        with archive.open("xb") as stream:
            for chunk in iter(lambda: upload.file.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, "备份压缩包不能超过 250 MB。")
                stream.write(chunk)
        result = inspect_archive(archive, folder / "inspected")
        result.pop("manifest")
        result.update(token=token, expires_at=(now() + TTL).isoformat())
        metadata = {"preview": result, "archive": digest(archive)}
        (folder / "preview.json").write_text(json.dumps(metadata), encoding="utf-8")
        return result
    except Exception:
        shutil.rmtree(folder)
        raise


def _result_file(token):
    return root() / "restore-receipts" / f"{token}.json"


def restore_backup(engine: Engine, token: str) -> dict:
    if not TOKEN.fullmatch(token):
        raise invalid("恢复凭证无效，请重新选择备份。")
    result_path = _result_file(token)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    # A DB receipt is committed in the same transaction as every restored row.
    # It handles a response loss or a crash before the filesystem receipt exists.
    with engine.connect() as connection:
        from sqlalchemy import text
        previous = connection.execute(text("SELECT result FROM restore_receipts WHERE token=:token"),
                                      {"token": token}).scalar()
    if previous is not None:
        result = json.loads(previous) if isinstance(previous, str) else previous
        _save_result(token, result)
        return result
    staging = root() / "backup-staging" / token
    try:
        metadata = json.loads((staging / "preview.json").read_text(encoding="utf-8"))
        if datetime.fromisoformat(metadata["preview"]["expires_at"]) <= now():
            raise HTTPException(410, "恢复预览已过期，请重新选择备份。")
        if digest(staging / "archive.zip") != metadata["archive"]:
            raise invalid("备份预览后的文件已改变，请重新选择备份。")
    except FileNotFoundError:
        raise HTTPException(410, "恢复预览不存在或已过期，请重新选择备份。") from None
    with TemporaryDirectory(prefix=".restore-work-", dir=root()) as temporary:
        unpacked = Path(temporary)
        inspected = inspect_archive(staging / "archive.zip", unpacked)
        asset_dir = root() / "restored" / uuid4().hex
        asset_dir.mkdir(parents=True)
        committed = False
        try:
            paths = {}
            for name in inspected["manifest"]["files"]:
                if name == "data.sqlite3":
                    continue
                destination = asset_dir / Path(name).name
                shutil.copyfile(unpacked / name, destination)
                paths[name] = str(destination)
            safety_name = f"lexi-read-safety-{uuid4().hex}.zip"
            export_backup(engine, root() / "safety-backups" / safety_name)
            result = {"restored": True, "safety_backup": safety_name,
                      "safety_backup_url": f"/api/backups/safety/{safety_name}"}
            with read_database(unpacked / "data.sqlite3") as source:
                _replace_data(engine, source, inspected["manifest"]["documents"], paths, token, result)
            committed = True
            _save_result(token, result)
            return result
        finally:
            if not committed:
                shutil.rmtree(asset_dir)


def _save_result(token, result):
    destination = _result_file(token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial")
    partial.write_text(json.dumps(result), encoding="utf-8")
    partial.replace(destination)


def _replace_data(engine, source, documents, paths, token, result):
    raw = engine.raw_connection()
    connection = raw.driver_connection
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        # Keep receipts from earlier restores even when restoring an older backup.
        previous = connection.execute("SELECT token,result,created_at FROM restore_receipts").fetchall()
        connection.execute("DELETE FROM schema_migrations")
        connection.executemany("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                               [tuple(row) for row in source.execute("SELECT version,applied_at FROM schema_migrations")])
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(f'DELETE FROM "{table.name}"')
        for table in Base.metadata.sorted_tables:
            columns = [column.name for column in table.columns]
            projection = ",".join(f'"{name}"' for name in columns)
            statement = f'INSERT INTO "{table.name}" ({projection}) VALUES ({",".join("?" for _ in columns)})'
            for row in source.execute(f'SELECT {projection} FROM "{table.name}"'):
                values = dict(row)
                if table.name == "documents":
                    name = documents[str(values["id"])]
                    values["image_path"] = paths[name] if name is not None else ""
                connection.execute(statement, [values[name] for name in columns])
        for receipt in previous:
            connection.execute("INSERT OR REPLACE INTO restore_receipts(token,result,created_at) VALUES (?,?,?)", receipt)
        connection.execute("INSERT INTO restore_receipts(token,result,created_at) VALUES (?,?,?)",
                           (token, json.dumps(result), now().isoformat()))
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise invalid("恢复数据的关联检查失败，已保留原来的数据。")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        raw.close()
