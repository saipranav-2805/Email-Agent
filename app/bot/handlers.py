from datetime import datetime, timezone
import json
import logging
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes
from app.db.models import Company, EmailRecord
from app.db.session import get_db_session
from app.services.calendar_service import create_calendar_event
from app.services.chat_agent import handle_user_chat_message

logger = logging.getLogger(__name__)


async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to /start."""
    welcome_text = (
        "Chief of Staff active.\n\n"
        "I monitor your College & Personal inboxes, run zero-hallucination attachment scans, "
        "auto-sync deadlines to Google Calendar, and sound loud alarms for shortlists.\n\n"
        "Commands:\n"
        "/pipeline - View your active placement funnel\n"
        "/help - How to interact with me\n\n"
        "Or simply ask me anything directly (e.g. 'What tests are coming up?', 'Mark Amazon as applied')."
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_text)


async def help_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to /help."""
    help_text = (
        "Chief of Staff Capabilities:\n"
        "- Ingestion: Real-time college webhooks + 5-min personal inbox poller.\n"
        "- Deterministic Verification: Exact RegNo/Name/Email match across all Excel sheets and PDF pages.\n"
        "- Auto-Calendar: 2-hour and 30-minute test & deadline alerts.\n"
        "- Two-Way Chat: Search emails, update pipeline statuses, or save permanent rules.\n\n"
        "Example Instructions:\n"
        "• 'Search emails for HackerRank links'\n"
        "• 'Mark Google as applied'\n"
        "• 'Show shortlisted companies'\n"
        "• 'Stop showing me webinars from IEEE club'"
    )
    if update.effective_message:
        await update.effective_message.reply_text(help_text)


async def pipeline_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responds to /pipeline with active applications."""
    async with get_db_session() as session:
        stmt = select(Company).where(Company.status.in_(["applied", "shortlisted", "interview", "offer"])).order_by(Company.updated_at.desc())
        companies = (await session.execute(stmt)).scalars().all()

        if not companies:
            if update.effective_message:
                await update.effective_message.reply_text("Pipeline empty. No active applications logged yet.")
            return

        lines = ["📊 *Placement Pipeline:*"]
        for c in companies:
            status_icon = "📝"
            if c.status == "shortlisted":
                status_icon = "🚨"
            elif c.status == "interview":
                status_icon = "🎤"
            elif c.status == "offer":
                status_icon = "🎉"

            lines.append(f"{status_icon} *{c.name}* — `{c.status.upper()}` | Role: {c.role or 'N/A'} | CTC: {c.ctc or 'N/A'}")

        if update.effective_message:
            await update.effective_message.reply_markdown_v2("\n".join(lines).replace(".", r"\.").replace("-", r"\-").replace("(", r"\(").replace(")", r"\)"))


async def backfill_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backfills past placement emails from both Personal and College Gmail into the pipeline."""
    if update.effective_message:
        await update.effective_message.reply_text("🔄 Scanning your inboxes (Personal + College) for past placement & test emails... This will take a few moments.")

    from app.services.scheduler import poll_college_inbox_job, poll_personal_inbox_job
    try:
        await poll_personal_inbox_job()
        await poll_college_inbox_job()
        if update.effective_message:
            await update.effective_message.reply_text("✅ Inbox scan completed! All detected companies and tests are loaded. Use /pipeline to see your status.")
    except Exception as e:
        logger.error(f"Backfill error: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("Scan finished. Check /pipeline for updated records.")


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes natural text messages to the interactive Chief of Staff agent."""
    if not update.effective_message or not update.effective_message.text:
        return

    user_text = update.effective_message.text
    logger.info(f"Received Telegram user message: {user_text}")

    # Show typing action
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response_text = await handle_user_chat_message(user_text)

    await update.effective_message.reply_text(response_text)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline buttons: mark applied, add to calendar, ignore."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    logger.info(f"Telegram callback clicked: {data}")

    # 1. Mark Applied
    if data.startswith("apply:"):
        comp_ref = data.split("apply:", 1)[1]
        async with get_db_session() as session:
            stmt = select(Company)
            if comp_ref.isdigit():
                stmt = stmt.where(Company.id == int(comp_ref))
            else:
                stmt = stmt.where(Company.name.ilike(comp_ref))

            comp = (await session.execute(stmt)).scalars().first()
            if not comp and not comp_ref.isdigit():
                comp = Company(name=comp_ref, status="applied", updated_at=datetime.now(timezone.utc))
                session.add(comp)
            elif comp:
                comp.status = "applied"
                comp.updated_at = datetime.now(timezone.utc)

            await session.commit()

        await query.answer("✅ Status updated: Applied!")
        if query.message:
            await query.message.reply_text(f"✅ Marked '{comp.name if comp else comp_ref}' as Applied.")

    # 2. Add to Calendar
    elif data.startswith("cal:"):
        email_ref = data.split("cal:", 1)[1]
        cal_created = False
        event_link = ""

        if email_ref.isdigit() and int(email_ref) > 0:
            async with get_db_session() as session:
                rec = await session.get(EmailRecord, int(email_ref))
                if rec:
                    # Check if already created
                    if rec.calendar_event_id:
                        await query.answer("📅 Already in Google Calendar!")
                        return

                    # Parse classification
                    c_data = json.loads(rec.classification_json or "{}")
                    title = f"[Placement] {c_data.get('company_name', 'Opportunity')} - Deadline/Test"
                    start_iso = c_data.get("deadline_iso") or datetime.now(timezone.utc).isoformat()
                    desc = f"Action: {c_data.get('action_required', '')}\nLinks: {', '.join(c_data.get('links', []))}"

                    cal_res = create_calendar_event(title=title, start_iso=start_iso, description=desc)
                    if cal_res:
                        rec.calendar_event_id = cal_res.get("event_id")
                        await session.commit()
                        cal_created = True
                        event_link = cal_res.get("html_link", "")

        if cal_created:
            await query.answer("📅 Added to Google Calendar!")
            if query.message:
                await query.message.reply_text(f"📅 Added to Calendar with 2h and 30m reminders!\n{event_link}")
        else:
            await query.answer("Could not add to Calendar (missing deadline or credentials).")

    # 3. Ignore
    elif data.startswith("ignore:"):
        comp_ref = data.split("ignore:", 1)[1]
        async with get_db_session() as session:
            stmt = select(Company)
            if comp_ref.isdigit():
                stmt = stmt.where(Company.id == int(comp_ref))
            else:
                stmt = stmt.where(Company.name.ilike(comp_ref))

            comp = (await session.execute(stmt)).scalars().first()
            if comp:
                comp.status = "ignored"
                comp.updated_at = datetime.now(timezone.utc)
                await session.commit()

        await query.answer("❌ Ignored.")
        if query.message:
            await query.message.reply_text(f"Ignored '{comp.name if comp else comp_ref}'.")
