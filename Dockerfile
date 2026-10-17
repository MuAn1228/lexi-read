FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_MODE=public \
    DATA_DIR=/var/data/lexi \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app

# CPU Paddle/OpenCV wheels require these shared libraries even without a GUI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ocr.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir -r requirements-ocr.txt

COPY backend/ ./backend/
COPY scripts/serve_public.py ./scripts/serve_public.py
COPY scripts/ocr_smoke.py ./scripts/ocr_smoke.py
COPY samples/reading.png ./samples/reading.png

EXPOSE 10000

CMD ["python", "scripts/serve_public.py"]
