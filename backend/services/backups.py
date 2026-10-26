"""Local complete backups with bounded validation and atomic SQLite restores."""
from datetime import datetime, timedelta, timezone
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import math
from pathlib import Path
import re
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
from threading import Condition
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
VOCABULARY_TOOL_TABLES = {"vocabulary_operations", "vocabulary_groups", "vocabulary_group_members"}
TTL = timedelta(minutes=30)
AUTOMATIC_INTERVAL = timedelta(hours=24)
AUTOMATIC_RETENTION = 7
AUTOMATIC = re.compile(r"^lexi-read-auto-(?P<stamp>\d{8}T\d{12}Z)-[a-f0-9]{32}\.zip$")
AUTOMATIC_STATE = "status.json"
AUTOMATIC_ERROR = "自动恢复点创建失败，请稍后重试或下载手动备份。"
AUTOMATIC_PRUNE_ERROR = "自动恢复点已创建，但旧恢复点暂时无法清理。"
AUTOMATIC_STATE_ERROR = "自动备份状态暂时不可用。"
AUTOMATIC_VERIFY_ERROR = "有自动恢复点校验失败，将在下次保障时重新创建。"
_AUTOMATIC_ERRORS = {AUTOMATIC_ERROR, AUTOMATIC_PRUNE_ERROR, AUTOMATIC_STATE_ERROR, AUTOMATIC_VERIFY_ERROR}
_automatic_condition = Condition()
_automatic_running: set[tuple[str, int]] = set()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomaticPoint:
    path: Path
    created_at: datetime
    size: int
    sha256: str


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
    actual = table_names(connection)
    previous_structure = actual == expected - VOCABULARY_TOOL_TABLES
    if actual != expected and not previous_structure:
        raise invalid("备份的数据结构与当前版本不一致，请使用相同版本的完整备份。")
    if {row[1] for row in connection.execute("PRAGMA table_info(schema_migrations)")} != {"version", "applied_at"}:
        raise invalid("备份的版本记录结构与当前版本不一致。")
    versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    supported = MIGRATIONS[:-1] if previous_structure and MIGRATIONS[-1][0] == "013_vocabulary_tools" else MIGRATIONS
    if versions != sorted(version for version, _ in supported):
        raise invalid("备份版本与当前版本不一致，请使用相同版本的完整备份。")
    for table in Base.metadata.sorted_tables:
        if table.name not in actual:
            continue
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
                    except (ValueError, TypeError, RecursionError):
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
        if document["source"] == "web":
            from backend.services.web_fetch import normalize_url
            try:
                if not document["source_url"] or normalize_url(document["source_url"]) != document["source_url"]:
                    raise ValueError()
            except (HTTPException, ValueError, TypeError):
                raise invalid("备份中的网页来源链接无效。") from None
        documents[document["id"]] = document
    for word in connection.execute("SELECT document_id,word,text_start,text_end,bbox,bbox_source FROM word_occurrences"):
        document = documents[word["document_id"]]
        if (word["bbox_source"] == "corrected_text" and document["source"] not in {"upload", "demo"}):
            raise invalid("备份中的校对单词来源无效。")
        if document["source"] in {"text", "pdf", "web"} or word["bbox_source"] == "corrected_text":
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
    if not previous_structure:
        from backend.services.vocabulary_backup_validation import validate_vocabulary_tools
        try:
            validate_vocabulary_tools(connection)
        except (ValueError, TypeError, KeyError):
            raise invalid("备份中的词库分组或撤销记录无效。") from None


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
            if document["source"] in {"text", "web"}:
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
                if document["source"] in {"text", "web"}:
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


def _automatic_folder(create=False) -> Path:
    folder = root() / "automatic-backups"
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_automatic_state() -> tuple[str | None, str | None, list[AutomaticPoint]]:
    """Read atomically written verification metadata; never trust filenames alone."""
    path = _automatic_folder() / AUTOMATIC_STATE
    if not path.is_file() or path.is_symlink():
        return None, None, []
    try:
        if path.stat().st_size > 16 * 1024:
            raise ValueError()
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError()
        error = state.get("error")
        attempted_at = state.get("attempted_at")
        if error is not None and error not in _AUTOMATIC_ERRORS:
            error = AUTOMATIC_STATE_ERROR
        if attempted_at is not None:
            parsed = datetime.fromisoformat(attempted_at)
            if parsed.tzinfo is None:
                raise ValueError()
        records = state.get("points", [])
        if not isinstance(records, list) or len(records) > AUTOMATIC_RETENTION:
            raise ValueError()
        folder, points, names = _automatic_folder(), [], set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {"filename", "created_at", "size", "sha256"}:
                raise ValueError()
            filename, created, size, checksum = (record["filename"], record["created_at"],
                                                   record["size"], record["sha256"])
            match = AUTOMATIC.fullmatch(filename) if isinstance(filename, str) else None
            if (not match or filename in names or type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES
                    or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
                raise ValueError()
            created_at = datetime.fromisoformat(created) if isinstance(created, str) else None
            stamped = datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
            if created_at is None or created_at.tzinfo is None or created_at != stamped:
                raise ValueError()
            point_path = folder / filename
            if not within(point_path, folder):
                raise ValueError()
            names.add(filename)
            points.append(AutomaticPoint(point_path, created_at, size, checksum))
        points.sort(key=lambda point: (point.created_at, point.path.name))
        return error, attempted_at, points
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return AUTOMATIC_STATE_ERROR, None, []


def _write_automatic_state(error: str | None, attempted_at: datetime,
                           points: list[AutomaticPoint]) -> None:
    folder = _automatic_folder(create=True)
    destination = folder / AUTOMATIC_STATE
    partial = folder / f".{AUTOMATIC_STATE}.{uuid4().hex}.partial"
    payload = {
        "version": 1,
        "attempted_at": attempted_at.isoformat(),
        "error": error,
        "points": [{"filename": point.path.name, "created_at": point.created_at.isoformat(),
                    "size": point.size, "sha256": point.sha256} for point in points],
    }
    try:
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        partial.replace(destination)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


def _verified_automatic_points(stored: list[AutomaticPoint] | None = None) -> tuple[list[AutomaticPoint], str | None]:
    """Return only metadata-owned points whose current bytes still match their digest."""
    if stored is None:
        _, _, stored = _read_automatic_state()
    folder = _automatic_folder()
    verified = []
    rejected = False
    for point in stored:
        try:
            valid_path = not point.path.is_symlink() and within(point.path, folder)
            details = point.path.stat() if valid_path else None
            valid_size = (details is not None and stat.S_ISREG(details.st_mode)
                          and details.st_size == point.size
                          and details.st_size <= MAX_ARCHIVE_BYTES)
            actual = digest(point.path) if valid_size else None
        except OSError:
            actual = None
        if actual == {"sha256": point.sha256, "size": point.size}:
            verified.append(point)
        else:
            rejected = True
    return verified, AUTOMATIC_VERIFY_ERROR if rejected else None


def latest_automatic_backup() -> AutomaticPoint | None:
    points, _ = _verified_automatic_points()
    return points[-1] if points else None


def _database_health(engine: Engine) -> tuple[str, str]:
    """Run SQLite's read-only consistency checks without exposing diagnostic rows."""
    if engine.dialect.name != "sqlite":
        return "unavailable", "unavailable"
    raw = None
    try:
        raw = engine.raw_connection()
        connection = raw.driver_connection
        quick = connection.execute("PRAGMA quick_check").fetchone()
        foreign = connection.execute("PRAGMA foreign_key_check").fetchone()
        return ("ok" if quick and quick[0] == "ok" else "failed",
                "ok" if foreign is None else "failed")
    except Exception:
        return "unavailable", "unavailable"
    finally:
        if raw is not None:
            raw.close()


def automatic_backup_status(engine: Engine, *, created=False) -> dict:
    stored_error, attempted_at, stored = _read_automatic_state()
    points, verification_error = _verified_automatic_points(stored)
    latest = points[-1] if points else None
    current = now()
    next_due = latest.created_at + AUTOMATIC_INTERVAL if latest else None
    quick, foreign = _database_health(engine)
    error = verification_error or stored_error
    return {
        "database_ok": quick == "ok" and foreign == "ok",
        "quick_check": quick,
        "foreign_key_check": foreign,
        "last_success_at": latest.created_at.isoformat() if latest else None,
        "last_size_bytes": latest.size if latest else None,
        "backup_count": len(points),
        "next_due_at": next_due.isoformat() if next_due else None,
        "due": verification_error is not None or latest is None or current >= next_due,
        "last_attempt_at": attempted_at,
        "error": error,
        "created": created,
        "retention": AUTOMATIC_RETENTION,
    }


def _create_automatic_backup(engine: Engine) -> AutomaticPoint:
    folder = _automatic_folder(create=True)
    created_at = now()
    stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = folder / f"lexi-read-auto-{stamp}-{uuid4().hex}.zip"
    if not AUTOMATIC.fullmatch(destination.name) or not within(destination, folder):
        raise RuntimeError("Invalid automatic backup destination")
    try:
        export_backup(engine, destination)
        with TemporaryDirectory(prefix=".automatic-check-", dir=root()) as temporary:
            inspect_archive(destination, Path(temporary))
        verified = digest(destination)
    except Exception:
        # Exclude a failed archive from recovery-point discovery even if deletion
        # is temporarily blocked by the operating system.
        quarantine = destination.with_suffix(".invalid")
        try:
            destination.replace(quarantine)
            quarantine.unlink(missing_ok=True)
        except OSError:
            destination.unlink(missing_ok=True)
        raise
    return AutomaticPoint(path=destination, created_at=created_at,
                          size=verified["size"], sha256=verified["sha256"])


def _discard_automatic_point(point: AutomaticPoint) -> bool:
    try:
        if (AUTOMATIC.fullmatch(point.path.name) and not point.path.is_symlink()
                and within(point.path, _automatic_folder())):
            point.path.unlink(missing_ok=True)
            return True
    except OSError:
        pass
    return False


def _prune_automatic_backups(points: list[AutomaticPoint]) -> bool:
    """Delete only metadata-owned archives after the retained state is durable."""
    failed = False
    for point in points:
        if not _discard_automatic_point(point):
            failed = True
    return not failed


def ensure_automatic_backup(engine: Engine, *, force=False) -> dict:
    """Create one due recovery point; overlapping callers share the same attempt."""
    key = (str(_automatic_folder().resolve()), id(engine))
    with _automatic_condition:
        if key in _automatic_running:
            _automatic_condition.wait_for(lambda: key not in _automatic_running)
            return automatic_backup_status(engine)
        status = automatic_backup_status(engine)
        if not force and not status["due"]:
            return status
        _automatic_running.add(key)
    created = False
    error = None
    attempted_at = now()
    point = None
    points, _ = _verified_automatic_points()
    try:
        try:
            point = _create_automatic_backup(engine)
            combined = sorted([*points, point], key=lambda item: (item.created_at, item.path.name))
            retained = combined[-AUTOMATIC_RETENTION:]
            discarded = combined[:-AUTOMATIC_RETENTION]
            # The archive becomes a recovery point only when its digest metadata
            # is atomically committed. Old metadata and files survive a failure.
            _write_automatic_state(None, attempted_at, retained)
            created = True
            if not _prune_automatic_backups(discarded):
                error = AUTOMATIC_PRUNE_ERROR
                try:
                    _write_automatic_state(error, attempted_at, retained)
                except OSError as state_exc:
                    # The digest metadata for the new point is already durable;
                    # do not roll it back merely because a warning could not save.
                    logger.error("Automatic backup warning could not be saved (%s)", type(state_exc).__name__)
        except Exception as exc:
            if point is not None and not created:
                _discard_automatic_point(point)
            error = AUTOMATIC_ERROR
            logger.error("Automatic recovery point creation failed (%s)", type(exc).__name__)
            try:
                _write_automatic_state(error, attempted_at, points)
            except OSError as state_exc:
                logger.error("Automatic backup status could not be saved (%s)", type(state_exc).__name__)
                error = AUTOMATIC_STATE_ERROR
        result = automatic_backup_status(engine, created=created)
        if error is not None:
            result["error"] = error
        return result
    finally:
        with _automatic_condition:
            _automatic_running.discard(key)
            _automatic_condition.notify_all()


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
        source_tables = table_names(source)
        if not VOCABULARY_TOOL_TABLES & source_tables:
            # A validated v012 archive restores into the current known schema;
            # new organization/undo tables start empty. No incoming DDL is run.
            connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                               ("013_vocabulary_tools", now().isoformat()))
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(f'DELETE FROM "{table.name}"')
        for table in Base.metadata.sorted_tables:
            if table.name not in source_tables:
                continue
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
