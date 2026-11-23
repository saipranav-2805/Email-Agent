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
from app.services.gmail_service import search_personal_gmail

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
            logger.warning(f"Live Gmail search error: {e}")

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


async def handle_user_chat_message(user_message: str) -> str:
    """Handles an interactive chat message from Telegram using Gemini with tool calling or direct fallback."""
    clean_msg = user_message.strip()
    if not clean_msg:
        return "Please send a message or instruction."

    # 1. Try Gemini with async client
    if settings.GEMINI_API_KEY:
        gemini_models = [settings.GEMINI_DEEP_MODEL, settings.GEMINI_FAST_MODEL, "gemini-3.5-flash-lite", "gemini-3.8-flash"]
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
                        system_instruction=SYSTEM_INSTRUCTION,
                        tools=[search_emails, get_company_pipeline, update_company_status, create_calendar_event, save_preference],
                        temperature=0.2,
                    ),
                )
                resp = await asyncio.wait_for(chat.send_message(clean_msg), timeout=3.5)
                if resp.text:
                    return resp.text
            except Exception as e:
                logger.warning(f"Gemini model {g_model} failed: {e}. Trying next fallback...")
                continue

    # 2. Fallback to Groq with tool calling
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
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
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
                            {"role": "system", "content": SYSTEM_INSTRUCTION},
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
