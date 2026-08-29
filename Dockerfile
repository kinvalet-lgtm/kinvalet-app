FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies (postgresql-client for schema init scripts)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install uv

COPY pyproject.toml .

# ── Development target ──────────────────────────────────────────────────────
FROM base AS development

RUN uv pip install --system -e ".[dev]"

COPY . .

# ── Production target ───────────────────────────────────────────────────────
FROM base AS production

RUN uv pip install --system -e .

COPY . .

# Make startup scripts executable
RUN chmod +x start.sh start-worker.sh

# Run as non-root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default: API. Railway overrides this per-service via Start Command setting.
CMD ["./start.sh"]
