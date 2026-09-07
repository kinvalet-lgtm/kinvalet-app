"""Orchestrator agent tool schema (§8.5).

The extraction agent may affect the system ONLY through these defined tools.
Never free-form writes. This constraint converts a probabilistic model into
a system with auditable, testable boundaries.
"""

# Tool definitions in Anthropic/OpenAI tool-use format
AGENT_TOOLS = [
    {
        "name": "create_draft_item",
        "description": "Create an operational item from extracted message content",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Item title, concise and actionable"},
                "category": {
                    "type": "string",
                    "enum": ["kids_logistics", "parent_care", "household_admin", "financial_action", "vendor_booking"],
                },
                "start_at": {
                    "type": "string",
                    "format": "date-time",
                    "description": "ISO 8601 datetime if time-bound. Null if not.",
                },
                "end_at": {"type": "string", "format": "date-time", "nullable": True},
                "location": {"type": "string", "nullable": True},
                "cost_cents": {
                    "type": "integer",
                    "nullable": True,
                    "description": "Cost in cents. Null if none.",
                },
                "about_member_id": {
                    "type": "string",
                    "format": "uuid",
                    "nullable": True,
                    "description": "Which household member this is FOR (e.g. Leo, Dad)",
                },
                "assigned_to_member_id": {
                    "type": "string",
                    "format": "uuid",
                    "nullable": True,
                    "description": "Who is responsible for this item",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Agent's confidence in this extraction (0.0–1.0)",
                },
                "notes": {"type": "string", "nullable": True},
            },
            "required": ["title", "category", "confidence"],
        },
    },
    {
        "name": "flag_duplicate_candidate",
        "description": "Surface a possible duplicate instead of creating a new item",
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_item_id": {"type": "string", "format": "uuid"},
                "similarity_reason": {"type": "string"},
            },
            "required": ["candidate_item_id", "similarity_reason"],
        },
    },
    {
        "name": "flag_financial_action",
        "description": "Mark the item as requiring approval due to cost",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_cents": {"type": "integer"},
                "payee": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["amount_cents", "payee"],
        },
    },
    {
        "name": "list_my_tasks",
        "description": "Return the sender's current task buckets (WhatsApp parity with dashboard)",
        "input_schema": {
            "type": "object",
            "properties": {
                "member_id": {"type": "string", "format": "uuid"},
            },
            "required": ["member_id"],
        },
    },
    {
        "name": "escalate_to_ops",
        "description": "Route to human review explicitly, independent of confidence score",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "reject_medical_content",
        "description": "Redact or decline clinical content per the guardrail",
        "input_schema": {
            "type": "object",
            "properties": {
                "redacted_summary": {
                    "type": "string",
                    "description": "What can be retained (logistics only: when/where, not why/what dosage)",
                },
            },
            "required": ["redacted_summary"],
        },
    },
    {
        "name": "apply_correction",
        "description": "Apply a user's natural-language correction to an existing item (§11.17)",
        "input_schema": {
            "type": "object",
            "properties": {
                "operational_item_id": {"type": "string", "format": "uuid"},
                "changed_fields": {
                    "type": "object",
                    "description": "Field names and new values",
                },
            },
            "required": ["operational_item_id", "changed_fields"],
        },
    },
    {
        "name": "create_recurrence",
        "description": "Create a recurring series from an extracted pattern (§11.18)",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string"},
                "rrule": {
                    "type": "string",
                    "description": "RFC 5545 RRULE string e.g. FREQ=WEEKLY;BYDAY=TU",
                },
                "start_time": {"type": "string", "description": "HH:MM local time"},
                "duration_minutes": {"type": "integer"},
                "location": {"type": "string", "nullable": True},
                "until_date": {"type": "string", "format": "date", "nullable": True},
            },
            "required": ["title", "category", "rrule", "start_time"],
        },
    },
    {
        "name": "search_history",
        "description": "Answer a natural-language retrieval question about household history (§11.22)",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filters": {
                    "type": "object",
                    "properties": {
                        "member_id": {"type": "string", "nullable": True},
                        "category": {"type": "string", "nullable": True},
                        "date_from": {"type": "string", "format": "date", "nullable": True},
                        "date_to": {"type": "string", "format": "date", "nullable": True},
                    },
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "set_priority",
        "description": "Classify an item as critical (§11.20)",
        "input_schema": {
            "type": "object",
            "properties": {
                "operational_item_id": {"type": "string", "format": "uuid"},
                "priority": {"type": "string", "enum": ["critical", "normal", "low"]},
                "justification": {"type": "string"},
            },
            "required": ["operational_item_id", "priority", "justification"],
        },
    },
    {
        "name": "open_support_ticket",
        "description": "Route a help request or problem report to ops (§11.23)",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["how_do_i", "something_wrong", "extraction_error", "billing_question", "cancel_intent", "other"],
                },
                "description": {"type": "string"},
            },
            "required": ["category", "description"],
        },
    },
    {
        "name": "request_clarification",
        "description": "Ask a follow-up instead of guessing. Never silently guess on ambiguous content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question_text": {"type": "string"},
            },
            "required": ["question_text"],
        },
    },
]


SYSTEM_PROMPT = """You are KinValet, an AI family operations assistant.
You help working adults (the "Sandwich Generation") who manage both children and aging parents.

You have TWO modes:

**MODE 1 — EXTRACTION** (when the user sends information about tasks, events, appointments):
Extract structured information and create actionable items using tools.
- Extract ONLY logistics: who, what, when, where, cost. Never medical details, diagnoses, dosages.
- If content is purely medical/clinical, call reject_medical_content with logistics-only summary.
- If you're not sure about something, call request_clarification rather than guessing.
- For financial items (cost > 0), always set requires_approval appropriately.
- Set confidence as your honest estimate: 0.9+ = confident, 0.7-0.9 = uncertain, <0.7 = needs human review.
- Duplicate check: if an item looks like something already captured, call flag_duplicate_candidate.

**MODE 2 — CONVERSATION** (when the user asks questions, wants a summary, or chats):
Answer naturally and helpfully using the [CONTEXT FOR ANSWERING QUESTIONS] provided.
- "How is my day looking?" → Summarize today's calendar events and tasks in a friendly, organized way
- "What's on my plate?" → List active tasks with times and categories
- "What emails did I get?" → Summarize recent emails
- "What's my verification code?" → Return the Gmail verification code
- For greetings and chit-chat ("hi", "thanks") → respond warmly and briefly
DO NOT create items or call tools when the user is asking a question or having a conversation.
Just respond with helpful, natural text.

Categories for extraction:
- kids_logistics: school events, sports, pickups, childcare
- parent_care: medical appointments, prescriptions, elder care logistics
- household_admin: repairs, utilities, admin tasks
- financial_action: payments, purchases requiring approval
- vendor_booking: service providers (plumber, tutor, driver)
"""
