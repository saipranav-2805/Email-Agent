# Placement & Inbox Chief of Staff — Complete Setup Guide

A production-grade, zero-cost, fault-tolerant personal AI Chief of Staff in Python (FastAPI). It monitors two Gmail inboxes (College Placement Mail + Personal Mail), parses attachments with deterministic zero-hallucination verification, auto-syncs Google Calendar events, sounds loud phone alarms for shortlists, and communicates via a two-way interactive Telegram Bot.

---

## Architecture Overview

```
+-------------------------------------------------------------------------------+
|                             DUAL-INBOX INGESTION                              |
+-------------------------------------------------------------------------------+
|  College Gmail (Apps Script)                Personal Gmail (Gmail API)        |
|  - Triggers on unread placement mail       - APScheduler poller every 5 mins  |
|  - Pushes base64 attachments (.xlsx, .pdf) - Zero-AI Gatekeeper filters promos|
|  - POST /ingest/college (X-API-KEY)        - High-signal keywords pass through|
+--------------------------------------+----------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                       DETERMINISTIC VERIFICATION & TRIAGE                     |
+-------------------------------------------------------------------------------+
|  1. Deduplication Engine: (source_inbox, message_id) dropped if already in DB |
|  2. Parser Engine: Exact boundary match for RegNo/Email/Name across all sheets|
|  3. Classifier: 4-tier triage (Gemini 2.5 Flash / Groq fallback)              |
|     - Safety Rule: Applied List != Shortlisted List (Applied lists demoted)   |
|     - Red Flags: Detects Vellore travel traps, bonds, service agreements      |
|     - Deadline Escalation: Eligible opportunities < 6h -> TIER 1              |
+--------------------------------------+----------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                         ACTIONS & NOTIFICATIONS                               |
+-------------------------------------------------------------------------------+
|  - TIER 1 (Shortlist / <6h Urgent): Telegram Alert + Calendar + Loud Alarm    |
|  - TIER 2 (New Opening / >=6h):     Telegram Alert + Calendar                 |
|  - TIER 3 (Webinar / Tech Talk):    Logged -> 9:00 PM IST Daily Digest        |
|  - TIER 4 (Noise / Ineligible):     Silently logged -> Counted in Daily Digest|
+--------------------------------------+----------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                        TWO-WAY TELEGRAM CHIEF OF STAFF                        |
+-------------------------------------------------------------------------------+
|  Interactive tool calling: search_emails, get_company_pipeline,               |
|  update_company_status, create_calendar_event, save_preference                |
+-------------------------------------------------------------------------------+
```

---

## 1. Google AI Studio (Free Gemini API Key)

1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **Get API key** -> **Create API key in new project**.
4. Copy your key and add it to `.env`:
   ```env
   GEMINI_API_KEY=AIzaSy...
   GEMINI_DEEP_MODEL=gemini-2.5-flash
   GEMINI_FAST_MODEL=gemini-2.5-flash-lite
   ```

---

## 2. Groq (Free Fallback LLM)

1. Go to [Groq Console](https://console.groq.com/).
2. Sign in and navigate to **API Keys**.
3. Click **Create API Key**, name it `placement-agent-fallback`.
4. Copy your key and add it to `.env`:
   ```env
   GROQ_API_KEY=gsk_...
   GROQ_MODEL=llama-3.3-70b-versatile
   ```

---

## 3. Telegram Bot Setup

### A. Create Bot via @BotFather
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`.
3. Choose a display name (e.g. `My Chief of Staff`).
4. Choose a username ending in `bot` (e.g. `pranav_chief_of_staff_bot`).
5. Copy the HTTP API Token into `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ
   ```

### B. Get Your Telegram User ID
1. Search for `@userinfobot` on Telegram.
2. Press `/start`.
3. Copy the numeric ID into `.env`:
   ```env
   TELEGRAM_CHAT_ID=987654321
   ```

### C. Webhook Secret Token
Generate a random secret string:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
Add it to `.env`:
```env
TELEGRAM_WEBHOOK_SECRET=your_generated_secret_token
```

---

## 4. Supabase (Free Tier PostgreSQL)

1. Go to [Supabase](https://supabase.com/) and open your project.
2. In the **new Supabase dashboard**, look at the top navigation bar and click the **Connect** button (top center/right of the screen).
   - Alternatively, click the **Settings (Gear icon ⚙️)** at the bottom of the left sidebar -> click **Database** -> scroll down to **Connection String**.
3. In the Connect popup:
   - Select the **URI** tab (or choose **Method: Direct connection** or **Session pooler**).
   - You will see a connection string like:
     `postgresql://postgres:[YOUR-PASSWORD]@db.xxxxxxx.supabase.co:5432/postgres`
     or (if using pooler):
     `postgresql://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres`
4. Copy the URI and replace `[YOUR-PASSWORD]` with your actual database password.
5. In your `.env` file, change `postgresql://` to `postgresql+asyncpg://`:
   ```env
   DATABASE_URL=postgresql+asyncpg://postgres:YOUR_ACTUAL_PASSWORD@db.xxxxxxx.supabase.co:5432/postgres
   ```
*(Pro-tip: If you are testing locally, you can also leave `DATABASE_URL=sqlite+aiosqlite:///./placement_agent.db`. The system handles local SQLite seamlessly with zero setup!)*

---

## 5. Google Cloud OAuth (Personal Gmail & Google Calendar)

This allows the agent to poll your personal Gmail, search test links, auto-create calendar events with 2h and 30m reminders, and send the loud alarm email.

### A. Create Project & Enable APIs
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project named `Placement-Chief-of-Staff`.
3. Go to **APIs & Services** -> **Library**:
   - Search for **Gmail API** -> Click **Enable**.
   - Search for **Google Calendar API** -> Click **Enable**.

### B. Configure OAuth Consent Screen & Add Test User (Crucial!)
1. Go to **APIs & Services** -> **OAuth consent screen** (or **Audience** in the new Google Cloud console).
2. Select User Type: **External** -> Click **Create**.
3. App name: `Placement Chief of Staff`, User support email: your personal Gmail (`saipranav.yellaram@gmail.com`).
4. **CRITICAL STEP (Prevents Error 403 access_denied):**
   - In the **OAuth consent screen** menu, scroll down to the **Test users** section (or click the **Audience** tab).
   - Click **+ ADD USERS**.
   - Enter your personal Gmail address: `saipranav.yellaram@gmail.com`.
   - Click **SAVE**.
   *(Google blocks login with "Access blocked: app has not completed verification" if your email is not explicitly in the Test Users list).*

### C. Create Credentials
1. Go to **APIs & Services** -> **Credentials** -> **Create Credentials** -> **OAuth client ID**.
2. Application type: **Desktop app**.
3. Name: `Placement Agent Desktop Client`.
4. Click **Create** and copy the **Client ID** and **Client Secret**.

### D. Generate Refresh Token in 1 Minute
Run the bundled token generator script:
```bash
.venv/bin/python scripts/get_google_oauth_token.py
```
Paste your `Client ID` and `Client Secret`.
1. A browser window opens. Select `saipranav.yellaram@gmail.com`.
2. When you see **"Google hasn’t verified this app"**:
   - Click **Advanced** (small link at bottom-left).
   - Click **Go to Placement Chief of Staff (unsafe)**.
   - Check the boxes to allow Gmail and Calendar permissions -> Click **Continue**.
3. The script will instantly print:
```env
GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REFRESH_TOKEN=1//0gXYZ...
PERSONAL_GMAIL_ADDRESS=saipranav.yellaram@gmail.com
```
Copy `GOOGLE_REFRESH_TOKEN` into your `.env`.

---

## 6. Google Apps Script Forwarder (`scripts/college_forwarder.gs`)

This script runs directly inside your college Google account without any local software.

1. Open your college Gmail account in your browser.
2. Go to [script.google.com](https://script.google.com/) and click **New Project**.
3. Replace the default content of `Code.gs` with the complete code from `scripts/college_forwarder.gs`.
4. Update the `CONFIG` section at the top:
   ```javascript
   const CONFIG = {
     BACKEND_URL: "https://your-deployed-app.onrender.com/ingest/college",
     API_KEY: "your_super_secret_college_ingest_key_here", // Matches COLLEGE_WEBHOOK_API_KEY in .env
     SEARCH_QUERY: 'in:inbox -label:AgentProcessed (placement OR "cdc" OR "pat" OR "career" OR shortlist OR interview OR assessment OR superset OR neopat OR hackerrank OR mettl OR "test link" OR "applied list" OR "shortlisted list")',
     PROCESSED_LABEL: "AgentProcessed",
     BATCH_SIZE: 15,
     ALLOWED_EXTENSIONS: ["xlsx", "xls", "csv", "pdf"]
   };
   ```
5. Click **Save** (disk icon).
6. Select `createTimeTrigger` from the function dropdown and click **Run**.
7. Grant the permissions requested by Google.
Done! Google Apps Script will now trigger every 5 minutes, extract spreadsheets and PDFs as base64, and post them securely to your FastAPI backend.

---

## 7. 1-Minute iOS Shortcuts Free Loud Alarm Bridge

To ensure you NEVER miss an interview shortlist or OA test link even if your iPhone is on **Silent**, **Do Not Disturb**, or a **Focus Mode**:

1. Open the **Shortcuts** app on your iPhone.
2. Tap the **Automation** tab at the bottom -> Tap **+** (New Automation).
3. Select **Email**.
   - Sender: Leave blank or set your personal Gmail.
   - Subject Contains: `[PLACEMENT-ALARM]`
   - Run Immediately: **Check** (Enable without confirmation).
4. Tap **Next** -> Choose **New Blank Automation** -> **Add Action**.
5. Add the following 3 actions:
   1. **Set Volume** -> Set to `100%`.
   2. **Play Sound** or **Start Timer** for `1 minute` (with timer sound set to "Radar" or "Alarm").
   3. **Show Notification** -> Title: `🚨 PLACEMENT SHORTLIST ALARM!`, Body: `Check Telegram & Gmail immediately!`
6. Tap **Done**.

Whenever a `TIER_1_CRITICAL` email is detected, the backend dispatches an email to your personal inbox with `[PLACEMENT-ALARM] <Company> - <Reason>`, instantly triggering your phone alarm at 100% volume!

---

## 8. Free 24/7 Cloud Deployment on Render + Keep-Alive

### Deploy on Render (Free Tier)
1. Push your repository to GitHub.
2. Go to [Render.com](https://render.com/) and click **New +** -> **Web Service**.
3. Connect your repository.
4. Settings:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** `Free`
5. Add your Environment Variables in the **Environment** tab (copy from `.env`).
6. Set `APP_BASE_URL` to your Render service URL (e.g. `https://placement-agent.onrender.com`).
7. Click **Create Web Service**.

### Free Keep-Alive via cron-job.org
Render free services spin down after 15 minutes of inactivity. To keep your poller and scheduler running 24/7 for zero cost:
1. Go to [cron-job.org](https://cron-job.org/) and create a free account.
2. Click **Create Cronjob**.
3. URL: `https://placement-agent.onrender.com/health`
4. Schedule: Every **10 minutes**.
5. Save. The `/health` endpoint will be pinged every 10 minutes, keeping the container warm and operational.

---

## 9. Local Development & Running Tests

### Setup Local Environment
```bash
# 1. Clone repository and navigate inside
cd "Placement & Inbox Chief of Staff"

# 2. Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy configuration
cp .env.example .env
# Edit .env with your keys

# 5. Run tests
pytest -v tests
```

### Start Local Server
```bash
uvicorn app.main:app --reload --port 8000
```
- Open Swagger Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Open Health Endpoint: [http://localhost:8000/health](http://localhost:8000/health)

---

## 10. Chief of Staff Telegram Bot Commands & Interactions

Message your Telegram bot naturally:
- `/pipeline` — View active applications, shortlists, and offers.
- `/help` — Overview of capabilities.
- `"Search emails for HackerRank test links"` — Live database and Gmail search.
- `"Mark Microsoft as applied, CTC 45 LPA"` — Updates application pipeline.
- `"Add Amazon interview tomorrow at 3 PM to calendar"` — Auto-syncs to Google Calendar.
- `"Stop showing me webinars from IEEE or sales jobs"` — Persists dynamic rule in DB for future triage.
- Every day at **9:00 PM IST**: Receives the Daily Digest with Tier 3 items and noise filter count.
- Every Sunday at **8:00 PM IST**: Receives the Weekly Placement Executive Briefing.
