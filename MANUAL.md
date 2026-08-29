# Sandwich Co-Pilot — Setup & Testing Manual

## Overview

Sandwich Co-Pilot is an AI-powered family operations platform for the "Sandwich Generation" — working adults managing both children and aging parents. It uses WhatsApp as the primary input surface and provides a web dashboard for visibility.

**Architecture:** Python modular monolith (FastAPI + SQLAlchemy + PostgreSQL), extractable to microservices post-pilot.

---

## Prerequisites

- Python 3.12+
- Docker & Docker Compose (for local Postgres)
- `uv` (fast Python package manager) or `pip`
- A Twilio account with WhatsApp Business API access (for SMS features)
- An Anthropic API key (for LLM extraction)

---

## Local Development Setup

### 1. Clone and Install

```bash
cd /Users/avinash/dev/kinet/sandwich-copilot

# Install uv (if not installed)
pip install uv

# Install all dependencies (including dev)
uv pip install --system -e ".[dev]"
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

**Minimum required for local development:**
```
DATABASE_URL=postgresql+asyncpg://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot
DATABASE_SYNC_URL=postgresql://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot
ENVIRONMENT=development
SECRET_KEY=any-random-string-for-local
SUPABASE_JWT_SECRET=any-random-string-for-local
```

### 3. Start the Database

```bash
docker-compose up postgres -d
# Wait for health check: "healthy"
docker-compose ps
```

### 4. Create Schemas and Run Migrations

```bash
# Create all module schemas
python -c "
import asyncio
from app.platform.db import AsyncSessionFactory, create_schemas
async def run():
    async with AsyncSessionFactory() as s:
        await create_schemas(s)
asyncio.run(run())
"
```

### 5. Start the API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at:
- `http://localhost:8000` — API base
- `http://localhost:8000/docs` — Swagger UI (development only)
- `http://localhost:8000/health` — Health check

### 6. Start the Worker (in a separate terminal)

```bash
python -m app.worker
```

### 7. Use Docker Compose (alternative — starts everything)

```bash
docker-compose up --build
# API: http://localhost:8000
# Metabase (analytics): docker-compose --profile metabase up
```

---

## Running Tests

### Unit Tests (fast, no database required)

```bash
pytest tests/unit/ tests/contract/ -v
```

### Contract Tests (verify module API conformance)

```bash
pytest tests/contract/ -v
```

Contract tests verify that every module's `api.py` satisfies its Protocol contract.
These are the load-bearing tests for the microservices extraction path.

### Integration Tests (require Postgres)

```bash
# Ensure postgres is running
docker-compose up postgres -d

# Run integration tests
pytest tests/integration/ -v -m integration
```

### All Tests with Coverage

```bash
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

### Import Boundary Check

```bash
# Verify no module imports another module directly (enforced in CI)
lint-imports
```

### Full CI locally

```bash
ruff check app/ tests/           # Lint
ruff format --check app/ tests/  # Format check
mypy --strict app/contracts/     # Type check contracts
lint-imports                     # Boundary enforcement
pytest tests/unit/ tests/contract/ -v  # Unit + contract tests
```

---

## Testing Key Flows

### 1. Register a Household

```bash
curl -X POST http://localhost:8000/api/v1/identity/register \
  -H "Content-Type: application/json" \
  -d '{
    "household_name": "The Miller Family",
    "timezone": "America/New_York",
    "display_name": "Sarah",
    "phone_e164": "+14155551234",
    "email": "sarah@example.com"
  }'
```

### 2. Simulate a WhatsApp Message

```bash
# In development, Twilio signature verification is skipped if no auth token is set
curl -X POST http://localhost:8000/api/v1/webhooks/twilio/whatsapp \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "MessageSid=SM123&From=whatsapp:+14155551234&Body=Leo has soccer practice Tuesday at 6pm at Field B"
```

### 3. Unregistered Sender (AC 11.3.1)

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/twilio/whatsapp \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "MessageSid=SM456&From=whatsapp:+19995550000&Body=Hello"
# Should receive onboarding invitation response within 2 seconds
```

### 4. List Items

```bash
# First get a JWT (via Supabase or the validate-token endpoint in dev)
curl -X GET http://localhost:8000/api/v1/operations/items \
  -H "Authorization: Bearer <your-jwt>"
```

### 5. Simulate Daily Briefing

```bash
# Trigger manually for testing
curl -X POST http://localhost:8000/api/v1/briefing/generate \
  -H "Authorization: Bearer <your-jwt>"
```

---

## Testing the Confidence Threshold

The extraction confidence threshold defaults to **0.90**. To test both paths:

**High-confidence path (auto-confirm):**
- Send a clear, unambiguous message: `"Leo has soccer practice Tuesday at 6pm at Field B"`
- Should auto-create an item and send a confirmation

**Low-confidence path (ops queue):**
- Temporarily lower `CONFIDENCE_THRESHOLD=0.5` in .env
- Or send an ambiguous message that the agent rates below threshold
- Should route to ops queue and return interim reply

---

## Architecture Reference

### Module Boundary Rules

1. **No cross-schema JOINs** — if you write `JOIN identity.household_member`, you've violated a boundary
2. **No cross-module imports** — `from app.modules.identity.models import Household` in `operations/` fails CI
3. **Only import from contracts** — `from app.contracts.identity import IdentityAPI` is the correct pattern
4. **Writes via events** — never call another module's write method directly; publish an event

### Module Structure (identical for all modules)

```
app/modules/{name}/
├── api.py          # PUBLIC — implements the contract Protocol
├── router.py       # PUBLIC — FastAPI routes
├── models.py       # PRIVATE — SQLAlchemy models (schema="{name}")
├── repository.py   # PRIVATE — all DB access
├── services/       # PRIVATE — domain logic
├── handlers.py     # Event subscriptions
└── migrations/     # This module's Alembic revisions
```

### Event Flow

```
1. Inbound webhook receives message
2. InboundMessage created → extraction job enqueued (same transaction)
3. Worker picks up job → LLM extraction runs (6s budget)
4. ExtractionCompleted event added to outbox (same transaction as extraction result)
5. Outbox dispatcher delivers → operations creates items
6. ItemCreated event → skills module schedules reminders
7. Skills fire → notification sends WhatsApp
```

---

## Deployment (Railway)

### Environment Variables to Set in Railway

Copy all variables from `.env.example` and set with production values.

### Deploy

```bash
# Push to main branch triggers Railway deployment
git push origin main
```

### Two Services in Railway

1. **api** — `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
2. **worker** — `python -m app.worker`

Both are always-on (no scale-to-zero):
- Cold starts would breach the <3s acknowledgement budget
- Briefings must fire at exactly 8:00 AM ±3 minutes

### Twilio Webhook URL

Set in Twilio Console:
```
https://your-api.railway.app/api/v1/webhooks/twilio/whatsapp
```

---

## Metabase (Internal Analytics)

Run locally against a read-only Postgres role:

```bash
docker-compose --profile metabase up metabase
# Access at: http://localhost:3001
```

Key dashboards to build:
- **OKR Dashboard:** O1 (30-day retention), O2 (tasks/household/week), O3 (briefing read rate)
- **Confidence Queue:** Extraction confidence distribution, ops review rate
- **Unit Economics:** Per-household WhatsApp cost, LLM cost, ops minutes
- **Connector Health:** Calendar sync status per household

---

## Troubleshooting

### Database connection fails
```bash
docker-compose ps  # Check postgres is healthy
docker-compose logs postgres
```

### Twilio signature verification fails
- Ensure `TWILIO_AUTH_TOKEN` is set correctly
- In development, signature verification is skipped if no token is configured
- Use `ngrok` to get a public URL for local Twilio testing: `ngrok http 8000`

### LLM calls fail
- Check `ANTHROPIC_API_KEY` is set
- Check LiteLLM logs for rate limiting
- LLM is only called in the worker (extraction task), not the API

### Import boundary violation
```
lint-imports
# Will show which module is importing from another module
# Fix by importing from app.contracts.{module} instead
```

### Briefing not firing
- Check worker logs: `docker-compose logs worker`
- Verify household timezone is set correctly
- Briefings fire at 8:00 AM household-local time ±3 min
- Manual trigger: run the `generate_briefing_task` directly in a shell

---

## Key Acceptance Criteria Quick Reference

| AC | What to verify |
|----|----------------|
| AC 3.1 | Dependent members have `auth_user_id IS NULL`, no phone route |
| AC 6.1 | Two households with messages at same minute route to correct households |
| AC 7.1 | Non-entitled connectors are ABSENT from UI (not disabled) |
| AC 11.1.1 | Registered member's first message is extracted, not greeted with invite |
| AC 11.3.1 | Unregistered number gets response in <2s |
| AC 11.3.2 | Photo of form → WhatsApp confirmation within 8 seconds |
| AC 11.3.3 | Item appears on dashboard within 2 seconds |
| AC 11.5.1 | Delegation accept → reassignment + notification within 8 seconds |
| AC 11.6.1 | Both calendars connected, conflict → auto-delegation proposed |
| AC 11.6.2 | Only one calendar connected, conflict → no delegation proposed to unconnected member |
| AC 11.12.1 | Archived item excluded from active views, retrievable via archive tab |
| AC 11.19.1 | STOP → all WhatsApp sends cease immediately |
| AC 11.19.2 | Quiet hours held; urgent breaks through and is logged |
