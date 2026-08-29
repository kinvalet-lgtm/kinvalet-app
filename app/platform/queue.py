"""Procrastinate job queue — Postgres-backed, no Redis.

At ~500 jobs/day this is the correct engineering choice: enqueue inside
the same DB transaction as the write, eliminating the dual-write problem.
Correct at this scale; outgrown only at thousands of jobs/second (4 OOM away).

Connector notes (procrastinate 2.x):
- SyncPsycopg2Connector was renamed to SyncPsycopgConnector
- AiopgConnector moved to procrastinate.contrib.aiopg.AiopgConnector
- AiopgConnector accepts dsn as a **kwarg (passed to aiopg.create_pool)
  and lazily creates the pool on first use — no explicit open() needed.
- The SQLAlchemy DATABASE_URL uses postgresql+asyncpg:// scheme; we strip
  the driver specifier to get a psycopg2-compatible URL for aiopg.
"""
import procrastinate
from procrastinate.contrib.aiopg import AiopgConnector

from app.platform.config import get_settings

_settings = get_settings()


def _aiopg_dsn(url: str) -> str:
    """Convert SQLAlchemy asyncpg URL to a bare postgresql:// DSN for aiopg."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


# Queue names
QUEUE_WEBHOOKS = "webhooks"       # fast, high priority — must ack Twilio <500ms
QUEUE_EXTRACTION = "extraction"   # LLM-bound, slow
QUEUE_NOTIFICATIONS = "notifications"  # outbound WhatsApp sends
QUEUE_SCHEDULED = "scheduled"     # skills, briefings — time-driven
QUEUE_MAINTENANCE = "maintenance"  # archive, purge, snapshots

# Sync app (for CLI / migration scripts)
queue_app = procrastinate.App(
    connector=procrastinate.SyncPsycopgConnector(),
)

# Async app (for API / worker process).
# Pool is lazily created on first defer_async() call.
async_queue_app = procrastinate.App(
    connector=AiopgConnector(dsn=_aiopg_dsn(_settings.database_url)),
)


def get_queue_app() -> procrastinate.App:
    return async_queue_app
