"""Explicitly download the optional KittenTTS Micro voice files.

Only the three pinned public model assets are downloaded. This command does not
install Python packages or start the backend. With --data-dir it uses only the
standard library and never loads the application's settings or database.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REVISION = "1ccf72b2c2048fd17efac7de2fab32d10e225084"
RELEASE = f"https://huggingface.co/KittenML/kitten-tts-micro-0.8/resolve/{REVISION}/"
MODEL_FOLDER = Path("tts") / "kitten-micro-0.8"
# Model and voice hashes are the publisher's LFS object IDs. The small config
# hash is calculated from the exact bytes at the same immutable revision.
ASSETS = (
    ("kitten_tts_micro_v0_8.onnx", 41384970,
     "95481626fee1ba70ce683e69c534fc7cb38433c46ce42d3abbeafb4b9f1a4123"),
    ("voices.npz", 3278902,
     "112710c1be8ad0e967c190fb0fd95cbe5848ec4791b93209f20b28b7da20dac1"),
    ("config.json", 473,
     "1f0bd2208348f9211cb0da64fcd1536eb28228571cc6b09e767eb6e203a0a532"),
)


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def setup(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        # Resolve the user's configured data location only for a normal setup.
        # Explicit --data-dir runs remain independent of .env and application
        # dependencies, including when used to prepare an isolated benchmark.
        sys.path.insert(0, str(ROOT))
        from backend.config import get_settings
        data_dir = get_settings().storage_dir

    folder = Path(data_dir).expanduser().resolve() / MODEL_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    for name, expected_size, expected_hash in ASSETS:
        target = folder / name
        if target.is_file() and target.stat().st_size == expected_size and digest(target) == expected_hash:
            print(f"Verified existing {name}", flush=True)
            continue

        temporary = folder / f"{name}.{uuid4().hex}.part"
        print(f"Downloading {name} ({expected_size:,} bytes)...", flush=True)
        try:
            request = Request(RELEASE + name, headers={"User-Agent": "LexiRead-natural-voice-setup"})
            count = 0
            value = sha256()
            with urlopen(request, timeout=45) as response, temporary.open("xb") as stream:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    count += len(chunk)
                    if count > expected_size:
                        raise ValueError(f"{name} exceeds the pinned release size")
                    value.update(chunk)
                    stream.write(chunk)
            if count != expected_size or value.hexdigest() != expected_hash:
                raise ValueError(f"{name} does not match the pinned size and SHA256")
            # Keep any previous file intact until its verified replacement is
            # completely downloaded. The partial file is on the same volume.
            temporary.replace(target)
            print(f"Verified {name}", flush=True)
        finally:
            temporary.unlink(missing_ok=True)

    print(f"KittenTTS Micro voice files are verified in {folder}.", flush=True)
    print("This setup installs model files only; the optional runtime is installed separately.", flush=True)
    return folder


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path,
        help="Backend data directory; files go in its tts/kitten-micro-0.8 subdirectory.",
    )
    args = parser.parse_args()
    setup(args.data_dir)


if __name__ == "__main__":
    main()
