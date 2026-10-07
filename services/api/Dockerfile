# API, worker and migration image: same code, different command.
# Build context is the repo root, because the uv workspace and lockfile live there:
#   docker build -f services/api/Dockerfile .

FROM python:3.12.15-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, in their own layer, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
COPY services/api/pyproject.toml services/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api --no-install-workspace

COPY services/api services/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package api

FROM python:3.12.15-slim-bookworm
RUN useradd --create-home --uid 10001 app
# Same path as the build stage: the app is installed in editable mode, and
# migrations are found next to the package.
COPY --from=build --chown=app:app /app /app
WORKDIR /app/services/api
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
