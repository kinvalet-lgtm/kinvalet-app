# Sandwich Co-Pilot

**AI-powered family operations platform for the Sandwich Generation.**

> Working adults managing both children and aging parents — eliminating 12+ hours/week of administrative overhead via WhatsApp and a web dashboard.

## Production

| Resource | URL |
|----------|-----|
| **API** | https://api-production-57040.up.railway.app |
| **Health** | https://api-production-57040.up.railway.app/health |
| **API Docs** | https://api-production-57040.up.railway.app/docs *(not available in production — use local)* |
| **Railway Project** | https://railway.com/project/45ddd7e5-67d8-4b85-a4a4-76f82b27f8ad |
| **Twilio Webhook** | `https://api-production-57040.up.railway.app/api/v1/webhooks/twilio/whatsapp` |

**Services:** 2 always-on containers (api + worker) + PostgreSQL on Railway Hobby

---

## Quick Start

```bash
# 1. Clone and install
cd sandwich-copilot
pip install uv && uv pip install --system -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env — minimum: DATABASE_URL, SUPABASE_JWT_SECRET, ANTHROPIC_API_KEY, TWILIO credentials

# 3. Start database
docker-compose up postgres -d

# 4. Start API
uvicorn app.main:app --reload

# 5. Start worker (separate terminal)
python -m app.worker
```

**API docs:** `http://localhost:8000/docs`

For full setup details see [MANUAL.md](MANUAL.md).

---

## What It Does

| Surface | Who | What |
|---------|-----|------|
| **WhatsApp** | Household members | Send photos, text, PDFs → AI extracts tasks, schedules, costs |
| **Dashboard** | Household members | View tasks, calendar, approve expenses, manage settings |
| **Ops Console** | Internal team | Review low-confidence AI extractions, triage failures |
| **Admin Console** | Leadership | Portfolio metrics, connector entitlements, feature flags |

**Core loop:**
1. Sarah photos a permission slip via WhatsApp
2. AI extracts: "Leo's field trip, Oct 15, $12"
3. Sarah taps ✅ Approve — event added to calendar, reminder scheduled
4. Next morning at 8 AM: *"Today: Leo's field trip — leave by 7:45 AM (22 min traffic)"*

---

## Architecture

**Modular monolith** — extractable to microservices with hours of work, not months.

```
app/
├── platform/          # Shared kernel (DB, events, config, tenancy)
├── contracts/         # THE ONLY cross-module coupling surface
└── modules/
    ├── identity/      # Households, members, phone routing
    ├── inbound/       # Twilio webhook, idempotency
    ├── extraction/    # LLM orchestration (Claude via LiteLLM)
    ├── operations/    # Core domain: items, approvals, delegation
    ├── connectors/    # Google/Microsoft Calendar, Plaid, email
    ├── skills/        # Reminder & Logistics/ETA (Google Maps)
    ├── notification/  # Outbound WhatsApp (Twilio)
    ├── briefing/      # Daily 8 AM briefing
    └── support/       # Help tickets, feedback
```

**The Prime Directive:** A module may never read or write another module's data directly. Cross-module reads go through a declared interface. Cross-module writes happen through events. There are no exceptions.

**Stack:**
- Language: Python 3.12 (locked)
- Framework: FastAPI + SQLAlchemy 2.x (async)
- Database: PostgreSQL via Supabase (one schema per module)
- Queue: procrastinate (Postgres-backed — no Redis)
- LLM: LiteLLM → Anthropic Claude
- Auth: Supabase Auth (JWT)
- Messaging: Twilio WhatsApp Business API
- Hosting: Railway (API + Worker) + Vercel (Next.js frontend)
- Analytics: Metabase (local, read-only role)

---

## Testing

```bash
pytest tests/unit/ tests/contract/ -v           # Fast (no DB)
pytest tests/integration/ -v -m integration     # Requires Postgres
lint-imports                                     # Boundary enforcement
mypy --strict app/contracts/                     # Type-check contracts
ruff check app/ tests/                           # Lint
```

CI runs all of these on every PR. **A PR that couples two modules fails the build.**

---

## Key Design Decisions

See [DECISIONS.md](DECISIONS.md) for the full ADR log.

| Decision | Why |
|----------|-----|
| No Redis | Postgres queue (`procrastinate`) is atomic with DB writes — eliminates dual-write problem |
| No cross-schema FK | Physical prerequisite for future service extraction |
| Per-module Alembic dirs | Migration history travels with the module when extracted |
| Transactional outbox | Events commit atomically with domain writes — no phantom notifications |
| import-linter in CI | Boundary violations fail the build automatically |
| 0.90 confidence threshold | Tunable in pilot — tracked via cost ledger |

---

## Infrastructure Cost (Pilot)

| Item | Monthly |
|------|---------|
| Railway Hobby (2 containers) | $10–15 |
| Supabase (Postgres + Auth) | $0 |
| Vercel (frontend) | $0 |
| Metabase (local) | $0 |
| **Infrastructure total** | **~$12–17** |
| WhatsApp messaging (variable) | $300–800 |
| LLM (variable, with optimizations) | $50–150 |

---

## Pilot Scale

- **40–50 households** free of charge
- ~500 inbound messages/day
- 0.006 req/s average, peaks 5–10/s
- Briefings: 8:00 AM household-local time, ±3 minutes

**OKRs (Q3–Q4 2026):**
- O1 Retention: ≥80% 30-day active household retention
- O2 Value: ≥14 completed tasks/household/week
- O3 Habit: ≥85% briefing open rate within 60 minutes

---

## For Contributors

1. Read the [Architecture doc](context/[MVP][Architecture]%20Sandwich%20Co-Pilot%20—%20Backend%20Architecture.md)
2. Read the [MANUAL.md](MANUAL.md) for setup and testing
3. Before touching any code: run `lint-imports` to understand current boundary state
4. Never import from another module's `models.py` or `repository.py`
5. New cross-module reads → add a method to `app/contracts/{module}.py`
6. New cross-module writes → publish an event via the outbox

---

*Last updated: 2026-08-24*
