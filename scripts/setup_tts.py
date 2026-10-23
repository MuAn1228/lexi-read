"""Download the optional local English voice model from its official release.

Run with the backend's Python after installing requirements-tts.txt. No API keys,
learning records or sentence text are used by this one-time setup.
"""
from hashlib import sha256
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import get_settings

RELEASE = "https://huggingface.co/rhasspy/piper-voices/resolve/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/"
# Pinned publisher revision; model SHA256 values are the official LFS object IDs.
# JSON hashes are computed from the small configuration files at that revision.
ASSETS = (
    ("en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx", 63531379,
     "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22"),
    ("en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json", 4972,
     "141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860"),
    ("en/en_GB/cori/medium/en_GB-cori-medium.onnx", 63531379,
     "1899f98e5fb8310154f3c2973f4b8a929ba7245e722b3d3a85680b833d95f10d"),
    ("en/en_GB/cori/medium/en_GB-cori-medium.onnx.json", 4966,
     "e262c16d7f192f69d4edd6b4ef8a5915379e67495fcc402f1ab15eeb33da3d36"),
)


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def setup() -> None:
    folder = get_settings().storage_dir / "tts"
    folder.mkdir(parents=True, exist_ok=True)
    for source_path, expected_size, expected_hash in ASSETS:
        name = source_path.rsplit("/", 1)[-1]
        target = folder / name
        if target.is_file() and target.stat().st_size == expected_size and digest(target) == expected_hash:
            print(f"Verified existing {name}", flush=True)
            continue
        temporary = folder / f"{name}.{uuid4().hex}.part"
        print(f"Downloading {name} ({expected_size // 1024 // 1024} MiB)...", flush=True)
        try:
            request = Request(RELEASE + source_path, headers={"User-Agent": "LexiRead-local-voice-setup"})
            count = 0
            value = sha256()
            with urlopen(request, timeout=45) as response, temporary.open("xb") as stream:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    count += len(chunk)
                    if count > expected_size:
                        raise ValueError("Downloaded model exceeds the official release size")
                    value.update(chunk)
                    stream.write(chunk)
            if count != expected_size or value.hexdigest() != expected_hash:
                raise ValueError("Downloaded voice file does not match the official SHA256")
            temporary.replace(target)
            print(f"Verified {name}", flush=True)
        finally:
            temporary.unlink(missing_ok=True)
    print("Local English voice files are ready. Restart the backend to enable them.", flush=True)


if __name__ == "__main__":
    setup()
