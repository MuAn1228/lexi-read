"""Start the public API with one worker and persistent OCR caches.

Render supplies PORT and the environment variables declared in render.yaml.
For a local smoke check, set DATA_DIR to a new temporary directory and provide
an explicit CORS_ORIGINS. This launcher never downloads OCR models on startup.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    os.environ.setdefault("APP_MODE", "public")
    if os.environ["APP_MODE"] != "public":
        raise SystemExit("serve_public.py requires APP_MODE=public.")

    origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "").split(",") if origin.strip()]
    if not origins:
        raise SystemExit("Set CORS_ORIGINS to the frontend's explicit HTTPS origin.")
    for origin in origins:
        parsed = urlsplit(origin)
        if ("*" in origin or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise SystemExit("CORS_ORIGINS must contain explicit HTTP(S) origins without paths or wildcards.")

    try:
        port = int(os.environ.get("PORT", "10000"))
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be an integer between 1 and 65535.")

    data_dir = Path(os.environ.get("DATA_DIR", "/var/data/lexi")).expanduser()
    if not data_dir.is_absolute():
        raise SystemExit("DATA_DIR must be an absolute path on the persistent disk.")
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(data_dir / "models"))
    os.environ.setdefault("PADDLE_HOME", str(data_dir / "paddle"))
    os.environ.setdefault("XDG_CACHE_HOME", str(data_dir / "cache"))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    import uvicorn

    # A single process shares the SQLite database, request limits, and OCR engine.
    # Render forwards the client IP in X-Forwarded-For behind its HTTPS proxy.
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, workers=1,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
