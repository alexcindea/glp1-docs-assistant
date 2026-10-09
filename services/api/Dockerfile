# Two images from one codebase. Build context is the repo root, because the uv
# workspace and lockfile live there:
#   docker build -f services/api/Dockerfile --target api .      API (+ migrate)
#   docker build -f services/api/Dockerfile --target worker .   ingest worker (Docling)
# Both run the local embedding model (CPU PyTorch, ~1.2 GB of weights, downloaded
# once in its own stage). Only the worker carries Docling and its ~540 MB of models.

FROM python:3.12.15-slim-bookworm AS build-base
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/

# Dependencies first, in their own layer, so code changes don't reinstall them.
FROM build-base AS embed-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api --extra embed --no-install-workspace

# The embedding model, pinned in app/embedding_models.py. This stage depends only on
# that file and the dependencies, so a code change doesn't download it again.
FROM embed-deps AS embedding-model
COPY services/api/app/embedding_models.py services/api/docker/fetch_embedding_model.py /tmp/
RUN HF_HOME=/models /app/.venv/bin/python /tmp/fetch_embedding_model.py /tmp/embedding_models.py

FROM embed-deps AS build-api
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api --extra embed

FROM build-base AS build-worker
# OpenCV (used by RapidOCR) needs these to load, here and at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
ENV HF_HOME=/app/.cache/huggingface
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api --extra ingest --no-install-workspace
# The models depend only on the dependencies, so they are fetched before the code is
# copied: a code change doesn't download them again. Docling's go to HF_HOME,
# RapidOCR's into its package folder in .venv.
COPY services/api/docker/fetch_models.py /tmp/fetch_models.py
RUN /app/.venv/bin/python /tmp/fetch_models.py
COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api --extra ingest

FROM python:3.12.15-slim-bookworm AS worker
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app
# Separate layers, most stable first. Same paths as the build stage: the app is
# installed in editable mode. The embedding model joins Docling's in the same cache.
COPY --from=embedding-model --chown=app:app /models/hub /app/.cache/huggingface/hub
COPY --from=build-worker --chown=app:app /app/.cache /app/.cache
COPY --from=build-worker --chown=app:app /app/.venv /app/.venv
COPY --from=build-worker --chown=app:app /app/services /app/services
WORKDIR /app/services/api
# HF_HUB_OFFLINE: models are never downloaded at runtime; a missing one fails loudly.
# (RapidOCR has no such switch; its models are baked in, checked by a
# `docker run --network none` load.)
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_OFFLINE=1
USER app
CMD ["python", "-m", "app.worker"]

FROM python:3.12.15-slim-bookworm AS api
RUN useradd --create-home --uid 10001 app
COPY --from=embedding-model --chown=app:app /models /app/.cache/huggingface
# Same path as the build stage: the app is installed in editable mode, and
# migrations are found next to the package.
COPY --from=build-api --chown=app:app /app /app
WORKDIR /app/services/api
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_OFFLINE=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
