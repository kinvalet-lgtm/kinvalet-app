# Architectural Decisions Log

This document records all significant architectural decisions made during implementation.
Every decision traces back to the Architecture doc or PRD, with reasoning.

---

## ADR-001: Python Modular Monolith (Locked)

**Status:** Decided (locked — no future migration)
**Date:** 2026-08-24

**Decision:** Python-only codebase. Modular monolith at MVP scale, extractable to microservices post-pilot.

**Reasoning (from Architecture §1):**
- Pilot scale is 40–50 households (~500 messages/day, 0.006 req/s average)
- This is 3–4 orders of magnitude below where distributed architecture benefits
- Goal: buy future optionality at near-zero present cost
- The modular structure (schema-per-module, no cross-schema JOINs, import-linter) makes extraction a day's work not a quarter's

---

## ADR-002: Postgres as Job Queue (No Redis)

**Status:** Decided
**Date:** 2026-08-24

**Decision:** Use `procrastinate` (Postgres-backed) instead of Redis + BullMQ (as PRD originally specified).

**Why this overrides the PRD:** The Architecture doc explicitly supersedes the PRD's technology stack section.

**Reasoning (from Architecture §8.1):**
- At 500 jobs/day, Redis provides no benefit
- Redis has a dual-write problem: enqueue + DB commit are separate operations → data loss risk
- With Postgres queue: enqueue is PART of the DB transaction → atomic, correct by construction
- Cost: eliminates one always-on service (~$5-10/month saved)
- Correctness: outgrown only at thousands of jobs/second (4 orders of magnitude away)

---

## ADR-003: No Cross-Schema Foreign Keys

**Status:** Decided (the Prime Directive)
**Date:** 2026-08-24

**Decision:** No foreign keys across module schemas. Store UUIDs as plain columns. Validate through module interfaces at write time.

**Reasoning (from Architecture §7.4):**
```python
# FORBIDDEN — physically prevents splitting databases later
assigned_to_member_id = mapped_column(ForeignKey("identity.household_member.id"))

# CORRECT — store ID, validate through interface
assigned_to_member_id: Mapped[UUID | None] = mapped_column(PgUUID, nullable=True, index=True)
```

**Consequence accepted:** Referential integrity across modules is the application's responsibility. Orphaned references are possible. Mitigated by: validation at write time, soft deletes, weekly reconciliation query.

---

## ADR-004: Per-Module Migration Directories

**Status:** Decided
**Date:** 2026-08-24

**Decision:** One Alembic migration directory per module (inside the module's own directory), not a shared top-level migrations/ folder.

**Reasoning (from Architecture §7.3):**
When `operations` becomes its own service, its Alembic history is already self-contained. A shared directory would mean untangling one interleaved history by hand with production data at stake — "a data modelling project."

---

## ADR-005: Dashboard Polling (No WebSockets)

**Status:** Decided
**Date:** 2026-08-24

**Decision:** Short polling every 30 seconds while the browser tab is focused. No WebSockets, no SSE.

**Reasoning (from Architecture §8.4):**
- At 50 households this is negligible load
- WhatsApp is the primary surface — dashboard is read/audit/configure
- Push can be added later without touching the data model
- Pause polling when tab is hidden

---

## ADR-006: Transactional Outbox for Cross-Module Writes

**Status:** Decided
**Date:** 2026-08-24

**Decision:** All cross-module writes go through a transactional outbox table. No direct function calls between modules for writes.

**Reasoning (from Architecture §6.2):**
1. **Correctness today:** If you write to DB then call a function that sends WhatsApp, and the transaction rolls back, you've sent a message about something that never happened.
2. **Extraction tomorrow:** The outbox is already an async, at-least-once, out-of-process delivery mechanism. When notification becomes its own service, the dispatcher publishes to a broker — publishing code and event schemas stay unchanged.

**Obligation:** All handlers must be idempotent. Handlers record processed event IDs in dedupe table.

---

## ADR-007: LiteLLM for LLM Access

**Status:** Decided
**Date:** 2026-08-24

**Decision:** All LLM calls go through LiteLLM behind an internal Protocol. Business logic never imports litellm directly.

**Routing:**
- CLASSIFY (chit-chat, intent, triage): `claude-haiku-4-5` — cheap
- EXTRACT_TEXT, EXTRACT_VISION: `claude-sonnet-4-6` — capable
- BRIEFING: `claude-haiku-4-5` — batched, cost-efficient

**Cost controls:**
1. Prompt caching — stable prefix (tool schema + household roster) cached
2. Batch briefing path — half price
3. Route by task — cheaper model for classification
4. Downscale images — 12MP phone photo costs vastly more than legible resolution
5. Skip LLM for deterministic answers — duplicate detection, recurrence expansion

---

## ADR-008: Single Operational Item Table (Tasks + Events Unified)

**Status:** Decided
**Date:** 2026-08-24

**Decision:** One `operational_item` table for both tasks and calendar events. Not two separate tables.

**Reasoning (from PRD §9.3):**
Most real household items need BOTH calendar semantics (time, location) AND task semantics (owner, approval, completion state). A permission slip has a due date AND a cost AND an assignee. Splitting them forces a synchronization layer between two tables representing the same real-world object — a reliable source of drift bugs.

---

## ADR-009: Confidence Threshold at 0.90 (Configurable)

**Status:** Decided (initial; to be tuned in pilot)
**Date:** 2026-08-24

**Decision:** Extraction confidence threshold starts at 0.90. Below this, route to human ops queue.

**From PRD §23.2:** This is genuinely undecided and must be treated as a tunable parameter from day one. The threshold determines the volume of ops work — too high means constant human review; too low means erroneous auto-confirmations. It is an economic decision tracked via `ops_minutes` in the cost ledger.

**Tracking:** Every extraction result records its confidence score, model name, and whether it required human review. This data drives threshold tuning.

---

## ADR-010: Import-Linter in CI (Non-Negotiable)

**Status:** Decided (enforced in CI)
**Date:** 2026-08-24

**Decision:** `lint-imports` is a required CI check. A PR that couples two modules FAILS the build.

**From Architecture §3.2:**
"Discipline alone does not survive a deadline. Three independent mechanisms, each catching what the others miss."

The other two mechanisms:
- Physical: schema-per-module (cross-schema JOIN is visible in code review)
- Structural: contracts package (only legitimate coupling surface)

---

## ADR-011: Dependent Members Cannot Authenticate (DB Constraint)

**Status:** Decided (AC 3.1)
**Date:** 2026-08-24

**Decision:** `auth_user_id IS NULL` enforced for `dependent_minor` and `dependent_care_recipient` roles. No `phone_channel_route` rows permitted. Enforced at DB level, not application policy.

**Reasoning:** This handles children's and elderly parents' data. A self-serve "lost everything" flow is the standard account-takeover vector. Dependents must never authenticate — this is a structural guarantee, not a policy one.

---

## ADR-012: Phone Route Deactivation (Not Deletion)

**Status:** Decided
**Date:** 2026-08-24

**Decision:** `phone_channel_route.is_active = false` on member suspend/remove, never delete the row.

**Reasoning:**
- Preserves audit trail
- If a carrier reassigns an old number to a stranger, the deactivated route prevents them from inheriting the previous household's access
- Historical attribution remains intact

---

## Open Questions (from PRD §23)

| # | Question | Status |
|---|----------|--------|
| 23.1 | STT provider: Whisper vs Deepgram | Voice notes deferred from MVP |
| 23.2 | Confidence threshold tuning | Starting at 0.90; tune in pilot |
| 23.4 | Plaid legal authority verification | Terms-of-Service matter, not technical |
| 23.5 | Old WhatsApp number shared externally | Documented limitation |
| 23.8 | Household lifecycle stage formula | Formula not finalized |
| 23.9 | Re-delegation hop cap | No cap in MVP; auditable |
| 23.11 | Google/Microsoft OIDC SSO | Pending ship decision |
