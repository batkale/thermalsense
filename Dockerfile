# ---- Stage 1: build the React frontend --------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# API_BASE resolves to '' in a production build, so the bundle calls the same
# origin it was served from — no backend URL is baked in.
RUN npm run build


# ---- Stage 2: python runtime -------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libgomp1 is required by XGBoost's OpenMP runtime; the slim image omits it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend/dist

# Writable state lives here — mount a volume at /data so the trained model,
# training buffer and beacon history survive redeploys.
ENV THERMALSENSE_DATA_DIR=/data \
    STATIC_DIR=/app/frontend/dist
RUN mkdir -p /data/models /data/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# --workers 1 is mandatory, not a default: the APRS thread and the in-memory
# glider state are per-process, so a second worker would open a duplicate
# upstream connection and serve divergent data.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
