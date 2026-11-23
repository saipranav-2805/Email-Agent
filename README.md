# Placement & Inbox Chief of Staff (Dual-Inbox AI Assistant)

A production-grade, zero-cost, fault-tolerant personal AI Chief of Staff in Python (FastAPI) that monitors two Gmail accounts (College Placement Mail + Personal Mail), tracks campus placements with zero errors, manages Google Calendar events, sounds loud phone alarms for shortlists, and communicates via a two-way interactive Telegram Bot.

---

## Key Features

1. **Deterministic Attachment Parsing Engine (`app/services/parser.py`):**
   - **Zero-Hallucination:** Excel (`.xlsx`, `.xls`, multi-sheet), CSV, and PDF (`pdfplumber` primary, `pypdf` fallback) are scanned with deterministic Python code.
   - Searches every sheet/page for exact case-insensitive matches of `reg_no` (with regex boundary checks so `21BCE1001` never falsely matches `21BCE10010`), `email`, or `full_name`.
   - Raw files are never dumped to the LLM to guess shortlist status.

2. **Strict Structured Output & Dual LLM Triage (`app/services/classifier.py`):**
   - 4-Tier classification:
     - **TIER 1 (Critical):** Confirmed Shortlist (strictly requires deterministic attachment match + shortlist/interview header), online test link, or eligible deadline closing in `< 6 hours`.
     - **TIER 2 (Action):** New eligible company opening or NeoPAT notice (`>= 6 hours`).
     - **TIER 3 (Worth a Look):** High-signal webinars or hackathons with a sharp 1-sentence human opinion on why it's worth time.
     - **TIER 4 (Noise):** Ineligible criteria, promotional spam, or mass circulars (silently recorded).
   - Strict Pydantic output schemas via `google-genai` with exponential backoff and automatic fallback to Groq (`llama-3.3-70b-versatile`).
   - "Applied List" vs "Shortlisted List" safety enforcement ensuring applied candidate lists never trigger a shortlist alarm.

3. **Dual-Inbox Architecture & Spam Gatekeeper (`app/services/gatekeeper.py`):**
   - **College Inbox Webhook:** `POST /ingest/college` secured by `X-API-KEY`, powered by `scripts/college_forwarder.gs`.
   - **Personal Inbox Poller:** APScheduler job runs every 5 minutes with Zero-AI Gatekeeper to block promotional blasts (`CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `List-Unsubscribe`) unless the sender/subject matches a tracked company or contains high-signal hiring keywords.
   - **Cross-Inbox Memory:** Automatically links openings on college mail with test links received on personal mail to the same company pipeline record.

4. **Notifications, Calendar & Free Loud Alarm Bridge:**
   - Scannable Telegram MarkdownV2 cards with status badges (`🚨 SHORTLISTED`, `🔴 URGENT (<6h)`, `🟡 ELIGIBLE — VELLORE TRAP`, `🟢 ELIGIBLE`).
   - Inline URL buttons (`[Open Form]`, `[Open NeoPAT]`, `[HackerRank]`) + Interactive Callback buttons (`[✅ Mark Applied]`, `[📅 Add to Calendar]`, `[❌ Ignore]`).
   - Automatic Google Calendar creation with 2-hour and 30-minute pop-up reminders.
   - For `TIER_1_CRITICAL`, automatically sends `[PLACEMENT-ALARM]` email to trigger an iOS Shortcuts automation running at 100% volume bypassing Silent/Focus modes.

5. **Two-Way Interactive Chief of Staff Agent (`app/services/chat_agent.py`):**
   - Natural language interaction in Telegram with tool calling:
     - `search_emails(query, inbox)`
     - `get_company_pipeline(status_filter)`
     - `update_company_status(company_name, status, notes)`
     - `create_calendar_event(title, start_iso, duration_mins, description)`
     - `save_preference(rule_text)` (persists custom rules like *"Ignore sales roles"*).

6. **Scheduled Briefings (IST Timezone):**
   - **Daily Digest (9:00 PM IST):** Summarizes Tier 3 opportunities with 1-line opinions and calendar buttons, plus a count of noise emails filtered.
   - **Weekly Placement Review (Sunday 8:00 PM IST):** Executive 7-day briefing covering applications submitted, active shortlists, avoided traps, and next week's focus areas.

---

## Directory Structure

```
.
├── .env.example
├── .gitignore
├── README.md
├── SETUP.md
├── pytest.ini
├── requirements.txt
├── config/
│   ├── profile.json
│   └── profile.example.json
├── scripts/
│   ├── college_forwarder.gs
│   └── get_google_oauth_token.py
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   └── models.py
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── telegram_app.py
│   │   ├── handlers.py
│   │   └── ui.py
│   └── services/
│       ├── __init__.py
│       ├── parser.py
│       ├── classifier.py
│       ├── gatekeeper.py
│       ├── processor.py
│       ├── chat_agent.py
│       ├── gmail_service.py
│       ├── calendar_service.py
│       └── scheduler.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_api.py
    ├── test_chat_agent.py
    ├── test_classifier.py
    ├── test_deduplication.py
    ├── test_gatekeeper.py
    ├── test_parser.py
    └── test_ui.py
```

---

## Quickstart

```bash
# 1. Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your keys (see SETUP.md for step-by-step free keys)

# 4. Run test suite
pytest -v tests

# 5. Start development server
uvicorn app.main:app --reload --port 8000
```

Refer to [`SETUP.md`](./SETUP.md) for full instructions on setting up free keys, Google Apps Script, Supabase, Google Cloud OAuth, Render deployment, and the 1-minute iOS Shortcuts loud alarm automation.
