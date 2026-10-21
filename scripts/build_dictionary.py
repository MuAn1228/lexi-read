"""Build the bundled, indexed ECDICT asset from an immutable upstream CSV.

This script uses only the standard library; it never reads application settings
or user vocabulary. See docs/third-party/ECDICT.md for provenance and commands.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
SOURCE_URL = f"https://raw.githubusercontent.com/skywind3000/ECDICT/{COMMIT}/ecdict.csv"
SOURCE_SHA256 = "1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf"
SPELLING = re.compile(r"[a-z]+(?:[' -][a-z]+)*", re.ASCII)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def spelling_key(value: str) -> str | None:
    key = " ".join(value.strip().lower().replace("’", "'").replace("–", "-").split())
    return key if len(key) <= 150 and SPELLING.fullmatch(key) else None


def senses(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.replace("\\n", "\n").splitlines() if part.strip()))


def merge_senses(left: str, right: str) -> str:
    return "\n".join(dict.fromkeys([*senses(left), *senses(right)]))


def build(source: Path, output_dir: Path, *, verify_source: bool = True) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_digest = sha256(source)
    if verify_source and source_digest != SOURCE_SHA256:
        raise ValueError("CSV checksum does not match the pinned ECDICT release")
    with tempfile.TemporaryDirectory(prefix="ecdict-build-") as temporary:
        database = Path(temporary) / "ecdict.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA page_size=4096")
        connection.execute("""CREATE TABLE entries (
            spelling TEXT PRIMARY KEY, headword TEXT NOT NULL,
            phonetic TEXT NOT NULL, chinese TEXT NOT NULL,
            english TEXT NOT NULL, exchange TEXT NOT NULL
        ) WITHOUT ROWID""")
        total = retained = merged = 0
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"word", "phonetic", "definition", "translation", "exchange"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("CSV does not contain the expected ECDICT columns")
            for row in reader:
                total += 1
                key = spelling_key(row["word"])
                chinese = "\n".join(senses(row["translation"]))
                if key is None or not chinese:
                    continue
                value = (key, row["word"].strip(), row["phonetic"].strip(), chinese,
                         "\n".join(senses(row["definition"])), row["exchange"].strip())
                try:
                    connection.execute("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)", value)
                    retained += 1
                except sqlite3.IntegrityError:
                    previous = connection.execute("SELECT * FROM entries WHERE spelling=?", (key,)).fetchone()
                    connection.execute("""UPDATE entries SET phonetic=?, chinese=?, english=?, exchange=?
                        WHERE spelling=?""", (
                            previous[2] or value[2], merge_senses(previous[3], value[3]),
                            merge_senses(previous[4], value[4]),
                            "/".join(dict.fromkeys(filter(None, [*previous[5].split("/"), *value[5].split("/")]))), key,
                        ))
                    merged += 1
        connection.commit()
        connection.execute("VACUUM")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        connection.close()
        asset = output_dir / "ecdict.sqlite3.gz"
        with database.open("rb") as source_stream, asset.open("wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0, compresslevel=9) as compressed:
                shutil.copyfileobj(source_stream, compressed)
        manifest = {
            "format_version": 1,
            "source": "ECDICT",
            "source_repository": "https://github.com/skywind3000/ECDICT",
            "source_commit": COMMIT,
            "source_url": SOURCE_URL,
            "source_sha256": source_digest,
            "license": "MIT",
            "source_records": total,
            "entries": retained,
            "merged_case_duplicates": merged,
            "omitted_records": total - retained - merged,
            "asset": asset.name,
            "asset_sha256": sha256(asset),
            "asset_bytes": asset.stat().st_size,
            "database_sha256": sha256(database),
            "database_bytes": database.stat().st_size,
            "sqlite_version": sqlite3.sqlite_version,
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local upstream ecdict.csv path")
    parser.add_argument("--download", action="store_true", help="Download the fixed upstream CSV to --source")
    parser.add_argument("--output", type=Path, default=ROOT / "backend/data_assets/dictionary")
    args = parser.parse_args()
    if args.download:
        args.source.parent.mkdir(parents=True, exist_ok=True)
        request = Request(SOURCE_URL, headers={"User-Agent": "LexiRead-dictionary-build"})
        with urlopen(request, timeout=120) as response, args.source.open("wb") as destination:
            shutil.copyfileobj(response, destination)
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
