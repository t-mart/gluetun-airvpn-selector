# Build stage: uv and a matching CPython, used only to populate /app/.venv
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

# Compile to bytecode for faster startup, copy out of the cache mount, skip
# development dependencies, and fail loudly instead of downloading a runtime.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer caches until the
# lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY . /app

# --no-editable installs the project into the venv, so the runtime stage needs
# nothing but the venv itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# Runtime stage: same CPython as the builder, without uv or build files
FROM python:3.14-alpine

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN adduser --system --no-create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app

EXPOSE 8081

CMD ["gluetun-airvpn-selector"]
