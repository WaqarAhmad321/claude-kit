# syntax=docker/dockerfile:1
FROM python:3.13.15-slim-trixie AS base

# GeoDjango loads GDAL, GEOS and PROJ from the system.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gdal-bin \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN useradd --system --uid 10001 --home-dir /app app

# The role matrix runs from this stage, with the dev dependencies.
FROM base AS test
RUN uv sync --frozen --no-install-project
USER app
ENTRYPOINT ["pytest", "-p", "no:cacheprovider", "tests/matrix"]

FROM base AS runtime
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
