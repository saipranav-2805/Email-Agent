import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import or_, select
from app.config import load_candidate_profile, settings
from app.db.models import Company, EmailRecord, ScheduledReminder, UserPreference
from app.db.session import get_db_session
from app.services.calendar_service import create_calendar_event as gcal_create_event
from app.services.gmail_service import search_college_gmail, search_personal_gmail

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------

async def tool_search_emails(query: str, inbox: str = "ALL") -> str:
    """Search database and live Gmail API for specific emails, companies, or test links."""
    results = []
    clean_query = query.strip()
    clean_inbox = inbox.strip().upper()

    async with get_db_session() as session:
        stmt = select(EmailRecord).where(
            or_(
                EmailRecord.subject.ilike(f"%{clean_query}%"),
                EmailRecord.body_text.ilike(f"%{clean_query}%"),
            )
        )
        if clean_inbox in ["COLLEGE", "PERSONAL"]:
            stmt = stmt.where(EmailRecord.source_inbox == clean_inbox)

        stmt = stmt.order_by(EmailRecord.received_at.desc()).limit(8)
        db_records = (await session.execute(stmt)).scalars().all()

        for rec in db_records:
            results.append({
                "source": f"DB ({rec.source_inbox})",
                "id": rec.id,
                "subject": rec.subject[:60],
                "sender": rec.sender[:40],
                "tier": rec.tier,
                "received_at": rec.received_at.strftime("%Y-%m-%d %H:%M"),
                "summary": (rec.body_text or "")[:120],
            })

    # Search live personal Gmail if requested
    if clean_inbox in ["PERSONAL", "ALL"] and settings.GOOGLE_REFRESH_TOKEN:
        try:
            live_items = await asyncio.to_thread(search_personal_gmail, clean_query, 5)
            for item in live_items:
                results.append({
                    "source": "Live Personal Gmail",
                    "subject": item["subject"][:60],
                    "sender": item["sender"][:40],
                    "date": item["date"],
                    "snippet": item["snippet"][:120],
                })
        except Exception as e:
            logger.warning(f"Live Personal Gmail search error: {e}")

    # Search live college Gmail if requested/all
    if clean_inbox in ["COLLEGE", "ALL"] and settings.COLLEGE_GOOGLE_REFRESH_TOKEN:
        try:
            live_college_items = await asyncio.to_thread(search_college_gmail, clean_query, 5)
            for item in live_college_items:
                results.append({
                    "source": "Live College Gmail",
                    "subject": item["subject"][:60],
                    "sender": item["sender"][:40],
                    "date": item["date"],
                    "snippet": item["snippet"][:120],
                })
        except Exception as e:
            logger.warning(f"Live College Gmail search error: {e}")

    if not results:
        return f"No emails found matching query '{query}' in {inbox} inbox."

    return json.dumps(results, indent=2)


async def tool_get_company_pipeline(status_filter: str = "") -> str:
    """List companies tracked in the placement pipeline, optionally filtered by status (e.g. applied, shortlisted, interview)."""
    async with get_db_session() as session:
        stmt = select(Company)
        if status_filter and status_filter.strip().lower() != "all":
            clean_status = status_filter.strip().lower()
            stmt = stmt.where(Company.status.ilike(clean_status))

        stmt = stmt.order_by(Company.updated_at.desc()).limit(20)
        companies = (await session.execute(stmt)).scalars().all()

        if not companies:
            return f"No companies found in pipeline with status '{status_filter or 'ANY'}'."

        output = []
        for c in companies:
            output.append({
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "role": c.role or "N/A",
                "ctc": c.ctc or "N/A",
                "notes": c.notes or "",
                "updated_at": c.updated_at.strftime("%Y-%m-%d"),
            })
        return json.dumps(output, indent=2)


async def tool_update_company_status(company_name: str, status: str, notes: str = "") -> str:
    """Manually mark a company as 'applied', 'shortlisted', 'interview', 'rejected', 'offer', or 'ignored'."""
    clean_name = company_name.strip()
    valid_statuses = ["interested", "applied", "shortlisted", "interview", "rejected", "offer", "ignored"]
    clean_status = status.strip().lower()
    if clean_status not in valid_statuses:
        return f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"

    async with get_db_session() as session:
        stmt = select(Company).where(Company.name.ilike(clean_name))
        company = (await session.execute(stmt)).scalars().first()

        if not company:
            # Create new company record
            company = Company(
                name=clean_name,
                status=clean_status,
                notes=notes,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(company)
            await session.commit()
            return f"Created new company '{clean_name}' with status: {clean_status}."

        company.status = clean_status
        if notes:
            company.notes = f"{company.notes}\n{notes}".strip() if company.notes else notes
        company.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return f"Updated '{company.name}' status to: {clean_status}."


async def tool_create_calendar_event(
    title: str,
    start_iso: str,
    duration_mins: int = 60,
    description: str = "",
) -> str:
    """Create custom reminders or test/interview events on Google Calendar."""
    cal_res = await asyncio.to_thread(
        gcal_create_event,
        title=title,
        start_iso=start_iso,
        duration_mins=duration_mins,
        description=description,
    )

    if not cal_res:
        return "Failed to create Google Calendar event (check credentials or start_iso format)."

    # Log to DB
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        async with get_db_session() as session:
            reminder = ScheduledReminder(
                company_name=title[:50],
                event_title=title,
                event_time=dt,
                calendar_event_id=cal_res.get("event_id"),
                status="synced",
            )
            session.add(reminder)
            await session.commit()
    except Exception as e:
        logger.debug(f"Could not persist reminder in DB: {e}")

    return f"Created Calendar Event '{title}'! Link: {cal_res.get('html_link')}"


async def tool_save_preference(rule_text: str) -> str:
    """Save permanent instructions or preferences to DB so future classifications obey them."""
    clean_rule = rule_text.strip()
    if not clean_rule:
        return "Preference rule cannot be empty."

    async with get_db_session() as session:
        pref = UserPreference(
            rule_text=clean_rule,
            category="user_instruction",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session.add(pref)
        await session.commit()

    return f"Saved permanent rule: '{clean_rule}'. All future triage calls will respect this."


# ---------------------------------------------------------
# Agent Orchestration
# ---------------------------------------------------------

SYSTEM_INSTRUCTION = """You are the personal Chief of Staff for candidate placement & inbox management.
Tone & Persona:
- Sharp, candid, human Chief of Staff.
- Zero robotic fluff, zero pleasantries ("Certainly! Here is...", "I'd be delighted to help!").
- High-signal, executive, concise.
- Direct answers with facts, deadlines, links, and actionable next steps.

You have access to tools to:
- search_emails(query, inbox)
- get_company_pipeline(status_filter)
- update_company_status(company_name, status, notes)
- create_calendar_event(title, start_iso, duration_mins, description)
- save_preference(rule_text)

Use these tools proactively whenever the user asks about emails, companies, applications, or setting reminders.
"""


AVAILABLE_TOOLS = [
    {
        "name": "search_emails",
        "description": "Search database and live Gmail API for specific emails, companies, or test links.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term, company name, or keyword"},
                "inbox": {"type": "string", "description": "Inbox to search: 'ALL', 'COLLEGE', or 'PERSONAL'", "default": "ALL"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company_pipeline",
        "description": "List companies tracked in the placement pipeline, optionally filtered by status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Optional filter: 'applied', 'shortlisted', 'interview', 'rejected', 'offer', 'interested', or 'all'",
                },
            },
        },
    },
    {
        "name": "update_company_status",
        "description": "Manually mark a company as 'applied', 'shortlisted', 'interview', 'rejected', 'offer', or 'ignored'.",
        "parameters": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "Target company name"},
                "status": {"type": "string", "description": "New status"},
                "notes": {"type": "string", "description": "Optional context or notes"},
            },
            "required": ["company_name", "status"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create custom reminders or test/interview events on Google Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start_iso": {"type": "string", "description": "ISO 8601 start datetime"},
                "duration_mins": {"type": "integer", "description": "Duration in minutes", "default": 60},
                "description": {"type": "string", "description": "Optional description"},
            },
            "required": ["title", "start_iso"],
        },
    },
    {
        "name": "save_preference",
        "description": "Save permanent instructions or preferences so future classifications obey them.",
        "parameters": {
            "type": "object",
            "properties": {
                "rule_text": {"type": "string", "description": "Rule or preference to enforce"},
            },
            "required": ["rule_text"],
        },
    },
]


async def execute_tool_call(name: str, args: Dict[str, Any]) -> str:
    """Executes a declared tool function by name."""
    try:
        if name == "search_emails":
            return await tool_search_emails(args.get("query", ""), args.get("inbox", "ALL"))
        elif name == "get_company_pipeline":
            return await tool_get_company_pipeline(args.get("status_filter", ""))
        elif name == "update_company_status":
            return await tool_update_company_status(
                args.get("company_name", ""),
                args.get("status", ""),
                args.get("notes", ""),
            )
        elif name == "create_calendar_event":
            return await tool_create_calendar_event(
                args.get("title", ""),
                args.get("start_iso", ""),
                args.get("duration_mins", 60),
                args.get("description", ""),
            )
        elif name == "save_preference":
            return await tool_save_preference(args.get("rule_text", ""))
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        logger.error(f"Error executing tool {name} with {args}: {e}")
        return f"Error executing tool {name}: {str(e)}"


async def build_inbox_context(user_message: str) -> str:
    """Proactively retrieves relevant emails, tracked companies, and live Gmail search results to ground the LLM in real data."""
    profile = load_candidate_profile()
    context_sections = [
        f"Candidate: {profile.name} (Reg: {profile.reg_no}, Branch: {profile.branch})",
    ]

    clean_msg = user_message.strip()

    # 1. Fetch tracked companies from DB
    try:
        async with get_db_session() as session:
            stmt = select(Company).order_by(Company.updated_at.desc()).limit(15)
            companies = (await session.execute(stmt)).scalars().all()
            if companies:
                comp_lines = [f"- {c.name}: Status={c.status}, Role={c.role or 'N/A'}, CTC={c.ctc or 'N/A'}" for c in companies]
                context_sections.append("Tracked Placement Companies in DB:\n" + "\n".join(comp_lines))
    except Exception as e:
        logger.debug(f"Context build DB company error: {e}")

    # 2. Fetch recent placement emails from DB
    try:
        async with get_db_session() as session:
            stmt = select(EmailRecord).order_by(EmailRecord.received_at.desc()).limit(8)
            recent_emails = (await session.execute(stmt)).scalars().all()
            if recent_emails:
                em_lines = [
                    f"- [{rec.source_inbox}] Subject: {rec.subject} | Tier: {rec.tier} | Received: {rec.received_at.strftime('%Y-%m-%d %H:%M')}\n  Body Snippet: {(rec.body_text or '')[:1200]}"
                    for rec in recent_emails
                ]
                context_sections.append("Recent Ingested Emails in DB:\n" + "\n".join(em_lines))
    except Exception as e:
        logger.debug(f"Context build DB email error: {e}")

    # 3. Live search if message appears to target a specific company or query
    stopwords = {"what", "is", "the", "tell", "me", "about", "did", "we", "get", "any", "mail", "email", "from", "for", "in", "my", "how", "to", "status", "deadline"}
    words = [w.strip("?,.!") for w in clean_msg.split() if w.lower().strip("?,.!") not in stopwords and len(w) >= 3]
    search_query = " ".join(words[:4]) if words else clean_msg

    if search_query:
        live_hits = []
        if settings.COLLEGE_GOOGLE_REFRESH_TOKEN:
            try:
                c_hits = await asyncio.to_thread(search_college_gmail, search_query, 3)
                for h in c_hits:
                    live_hits.append(f"- [College Mail] {h['subject']} (Date: {h.get('date', '')})\n  Snippet: {h.get('snippet', '')[:160]}")
            except Exception as e:
                logger.debug(f"Live college search error: {e}")

        if settings.GOOGLE_REFRESH_TOKEN:
            try:
                p_hits = await asyncio.to_thread(search_personal_gmail, search_query, 3)
                for h in p_hits:
                    live_hits.append(f"- [Personal Mail] {h['subject']} (Date: {h.get('date', '')})\n  Snippet: {h.get('snippet', '')[:160]}")
            except Exception as e:
                logger.debug(f"Live personal search error: {e}")

        if live_hits:
            context_sections.append(f"Live Gmail Search Results for '{search_query}':\n" + "\n".join(live_hits))

    return "\n\n".join(context_sections)


async def handle_user_chat_message(user_message: str) -> str:
    """Handles an interactive chat message from Telegram using Gemini or Groq with grounded email context."""
    clean_msg = user_message.strip()
    if not clean_msg:
        return "Please send a message or instruction."

    # Build grounded inbox & placement context
    inbox_context = await build_inbox_context(clean_msg)

    dynamic_system_prompt = (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"--- CURRENT REAL-TIME INBOX & PLACEMENT CONTEXT ---\n"
        f"{inbox_context}\n"
        f"---------------------------------------------------\n\n"
        f"STRICT RULES:\n"
        f"1. Ground your answer in the real emails and companies shown above.\n"
        f"2. If the user asks about a company, assessment, or test link, report the EXACT details from their inboxes.\n"
        f"3. If no email or record exists for what they asked, say clearly: 'I checked both your College and Personal mailboxes and found no records for [Topic] yet.'\n"
        f"4. NEVER hallucinate US tech salaries or generic corporate fluff unless specifically requested."
    )

    # 1. Try Gemini with async client
    if settings.GEMINI_API_KEY:
        gemini_models = [settings.GEMINI_DEEP_MODEL, settings.GEMINI_FAST_MODEL, "gemini-3.5-flash-lite"]
        unique_gemini = [m for i, m in enumerate(gemini_models) if m and m not in gemini_models[:i]]

        for g_model in unique_gemini:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=settings.GEMINI_API_KEY)

                # Tool wrappers
                async def search_emails(query: str, inbox: str = "ALL") -> str:
                    """Search database and live Gmail API for specific emails, companies, or test links."""
                    return await tool_search_emails(query, inbox)

                async def get_company_pipeline(status_filter: str = "") -> str:
                    """List companies tracked in the placement pipeline, optionally filtered by status."""
                    return await tool_get_company_pipeline(status_filter)

                async def update_company_status(company_name: str, status: str, notes: str = "") -> str:
                    """Manually mark a company as 'applied', 'shortlisted', 'interview', 'rejected', 'offer', or 'ignored'."""
                    return await tool_update_company_status(company_name, status, notes)

                async def create_calendar_event(title: str, start_iso: str, duration_mins: int = 60, description: str = "") -> str:
                    """Create custom reminders or test/interview events on Google Calendar."""
                    return await tool_create_calendar_event(title, start_iso, duration_mins, description)

                async def save_preference(rule_text: str) -> str:
                    """Save permanent instructions or preferences so future classifications obey them."""
                    return await tool_save_preference(rule_text)

                chat = client.aio.chats.create(
                    model=g_model,
                    config=types.GenerateContentConfig(
                        system_instruction=dynamic_system_prompt,
                        tools=[search_emails, get_company_pipeline, update_company_status, create_calendar_event, save_preference],
                        temperature=0.1,
                    ),
                )
                resp = await asyncio.wait_for(chat.send_message(clean_msg), timeout=15.0)
                if resp.text:
                    return resp.text
            except Exception as e:
                logger.warning(f"Gemini model {g_model} failed: {e}. Trying next fallback...")
                continue

    # 2. Fallback to Groq with tool calling & enriched context
    if settings.GROQ_API_KEY:
        groq_models = [settings.GROQ_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
        unique_groq = [m for i, m in enumerate(groq_models) if m and m not in groq_models[:i]]

        for model_choice in unique_groq:
            try:
                from groq import Groq
                groq_client = Groq(api_key=settings.GROQ_API_KEY)

                groq_tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["parameters"],
                        },
                    }
                    for t in AVAILABLE_TOOLS
                ]

                user_turn = {"role": "user", "content": clean_msg}
                groq_resp = await asyncio.to_thread(
                    groq_client.chat.completions.create,
                    model=model_choice,
                    messages=[
                        {"role": "system", "content": dynamic_system_prompt},
                        user_turn,
                    ],
                    tools=groq_tools,
                    tool_choice="auto",
                )

                msg = groq_resp.choices[0].message
                if msg.tool_calls:
                    tc = msg.tool_calls[0]
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments or "{}")
                    tool_output = await execute_tool_call(fn_name, fn_args)

                    second_resp = await asyncio.to_thread(
                        groq_client.chat.completions.create,
                        model=model_choice,
                        messages=[
                            {"role": "system", "content": dynamic_system_prompt},
                            user_turn,
                            msg,
                            {"role": "tool", "tool_call_id": tc.id, "content": tool_output},
                        ],
                    )
                    return second_resp.choices[0].message.content or "Done."
                elif msg.content:
                    return msg.content

            except Exception as groq_e:
                logger.warning(f"Groq fallback model {model_choice} failed: {groq_e}")
                continue

    # 3. Direct local intent handler (guarantees an intelligent answer even if internet/LLMs are down)
    lower_msg = clean_msg.lower()
    if any(k in lower_msg for k in ["pipeline", "status", "companies", "applied", "shortlist", "interview"]):
        pipeline_data = await tool_get_company_pipeline()
        return f"📊 *Placement Status:*\n{pipeline_data}"

    if "mark" in lower_msg or "applied" in lower_msg:
        words = clean_msg.split()
        comp = words[-1]
        return await tool_update_company_status(comp, "applied")

    if any(k in lower_msg for k in ["search", "find", "test link", "email"]):
        search_res = await tool_search_emails(clean_msg)
        return f"🔍 *Search Results:*\n{search_res}"

    return "Chief of Staff active and monitoring. Ask for status, search emails, or update application records."
