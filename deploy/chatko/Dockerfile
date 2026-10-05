# The hub itself (`chatko run`) for amd64 and arm64: pure Python, so the same file builds both.
#   docker compose -f deploy/docker-compose.yml build chatko
# The build context is the repository root (see deploy/docker-compose.yml and .dockerignore).
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
# The user comes from the compose file (`user:`), so that data/chatko belongs to the host user.
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
VOLUME /app/data
# `config/` is mounted read-only at /app/config; secrets arrive as environment variables.
# SIGTERM stops the hub in order (it finishes in a couple of seconds).
ENTRYPOINT ["chatko"]
CMD ["run", "--config", "/app/config/chatko.yaml", "--data", "/app/data"]
