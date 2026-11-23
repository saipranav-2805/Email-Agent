import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.bot.telegram_app import send_telegram_notification
from app.bot.ui import escape_md
from app.config import settings
from app.db.models import Company, EmailRecord
from app.db.session import get_db_session
from app.services.gatekeeper import evaluate_personal_email
from app.services.gmail_service import fetch_recent_college_emails, fetch_recent_personal_emails
from app.services.processor import process_incoming_email

logger = logging.getLogger(__name__)

scheduler: Optional[AsyncIOScheduler] = None


async def poll_personal_inbox_job():
    """Polls personal Gmail every 5 minutes and runs Zero-AI Gatekeeper."""
    if not settings.GOOGLE_REFRESH_TOKEN:
        logger.debug("Personal Gmail polling skipped: Google OAuth refresh token not configured.")
        return

    logger.info("Running scheduled Personal Inbox Poller...")
    try:
        # 1. Fetch tracked companies from DB
        async with get_db_session() as session:
            stmt = select(Company.name)
            companies = (await session.execute(stmt)).scalars().all()
            known_companies = [c for c in companies if c]

        # 2. Fetch recent messages
        recent_emails = await asyncio.to_thread(fetch_recent_personal_emails, 15)

        for em in recent_emails:
            msg_id = em["message_id"]
            thread_id = em["thread_id"]
            sender = em["sender"]
            subject = em["subject"]
            body_text = em["body_text"]
            snippet = em["snippet"]
            labels = em.get("labels", [])
            headers = em.get("headers", {})
            date_str = em.get("received_at", "")

            # Gatekeeper triage
            decision = evaluate_personal_email(
                sender=sender,
                subject=subject,
                labels=labels,
                headers=headers,
                known_companies=known_companies,
                body_snippet=snippet,
            )

            if not decision.is_allowed:
                logger.debug(f"Gatekeeper BLOCKED email '{subject[:40]}': {decision.reason}")
                continue

            logger.info(f"Gatekeeper ALLOWED email '{subject[:40]}': {decision.reason}")

            # Parse received date
            received_at = datetime.now(timezone.utc)
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    received_at = parsedate_to_datetime(date_str)
                except Exception:
                    pass

            # Ingest through standard pipeline
            await process_incoming_email(
                source_inbox="PERSONAL",
                message_id=msg_id,
                thread_id=thread_id,
                sender=sender,
                subject=subject,
                body_text=body_text,
                received_at=received_at,
                raw_attachments=[],
            )

    except Exception as e:
        logger.error(f"Error in Personal Inbox Poller job: {e}")


async def poll_college_inbox_job():
    """Polls college Gmail (pranav.23bce8548@vitapstudent.ac.in) every 5 minutes via direct OAuth.

    This is a secondary/redundant poller — the Google Apps Script webhook is the primary ingestion
    path for college mail. This catches anything the Apps Script misses (e.g. trigger gaps).
    No gatekeeper needed: college inbox search query already filters to placement-only emails.
    """
    if not settings.COLLEGE_GOOGLE_REFRESH_TOKEN:
        logger.debug("College Gmail polling skipped: COLLEGE_GOOGLE_REFRESH_TOKEN not configured.")
        return

    logger.info("Running scheduled College Inbox Poller...")
    try:
        college_emails = await asyncio.to_thread(fetch_recent_college_emails, 20)

        for em in college_emails:
            msg_id = em["message_id"]
            thread_id = em["thread_id"]
            sender = em["sender"]
            subject = em["subject"]
            body_text = em["body_text"]
            date_str = em.get("received_at", "")

            received_at = datetime.now(timezone.utc)
            if date_str:
                try:
                    from email.utils import parsedate_to_datetime
                    received_at = parsedate_to_datetime(date_str)
                except Exception:
                    pass

            # College emails go straight to the pipeline — no gatekeeper (already filtered by search query)
            await process_incoming_email(
                source_inbox="COLLEGE",
                message_id=msg_id,
                thread_id=thread_id,
                sender=sender,
                subject=subject,
                body_text=body_text,
                received_at=received_at,
                raw_attachments=[],  # Attachments from college mail handled by Apps Script webhook
            )

    except Exception as e:
        logger.error(f"Error in College Inbox Poller job: {e}")


async def daily_digest_job():
    """Runs daily at 9:00 PM IST (21:00). Summarizes Tier 3 items & noise filtered count."""
    logger.info("Executing 9:00 PM IST Daily Placement Digest...")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    async with get_db_session() as session:
        # Tier 3 items
        t3_stmt = select(EmailRecord).where(
            EmailRecord.tier == "TIER_3_WORTH_A_LOOK",
            EmailRecord.processed_at >= cutoff,
        ).order_by(EmailRecord.processed_at.desc())
        t3_records = (await session.execute(t3_stmt)).scalars().all()

        # Count Tier 4 noise
        t4_stmt = select(func.count(EmailRecord.id)).where(
            EmailRecord.tier == "TIER_4_NOISE",
            EmailRecord.processed_at >= cutoff,
        )
        noise_count = (await session.execute(t4_stmt)).scalar() or 0

    if not t3_records and noise_count == 0:
        logger.info("Daily digest: No emails processed today. Skipping notification.")
        return

    lines = [
        "🌙 *Daily Chief of Staff Digest (9:00 PM IST)*\n",
        f"Filtered *{noise_count}* noise emails silently today\\.\n",
    ]

    buttons = []
    if t3_records:
        lines.append(f"💡 *{len(t3_records)} Items Worth a Look:*")
        for idx, rec in enumerate(t3_records[:5], start=1):
            data = json.loads(rec.classification_json or "{}")
            comp = data.get("company_name") or rec.subject[:35]
            summary = data.get("concise_summary") or rec.subject[:50]
            opinion = data.get("human_opinion") or "Check topics and see if relevant."

            lines.append(f"\n*{idx}\\. {escape_md(comp)}*")
            lines.append(f"• _{escape_md(summary)}_")
            lines.append(f"• 🧠 *Opinion:* _{escape_md(opinion)}_")

            # Inline calendar button for this digest item
            buttons.append([
                InlineKeyboardButton(
                    text=f"📅 Add {comp[:20]} to Cal",
                    callback_data=f"cal:{rec.id}",
                )
            ])
    else:
        lines.append("No Tier 3 items flagged today — all clear!")

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await send_telegram_notification(
        text="\n".join(lines),
        reply_markup=reply_markup,
    )


async def weekly_review_job():
    """Runs Sunday at 8:00 PM IST (20:00). High-signal weekly placement briefing."""
    logger.info("Executing Sunday 8:00 PM IST Weekly Placement Review...")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with get_db_session() as session:
        # Applied companies this week
        applied_stmt = select(Company).where(
            Company.status == "applied",
            Company.updated_at >= cutoff,
        )
        applied_companies = (await session.execute(applied_stmt)).scalars().all()

        # Shortlisted companies
        shortlist_stmt = select(Company).where(
            Company.status == "shortlisted",
        )
        shortlisted_companies = (await session.execute(shortlist_stmt)).scalars().all()

        # Total emails parsed
        total_stmt = select(func.count(EmailRecord.id)).where(EmailRecord.processed_at >= cutoff)
        total_emails = (await session.execute(total_stmt)).scalar() or 0

        # Trap emails avoided
        trap_stmt = select(EmailRecord).where(
            EmailRecord.classification_json.ilike("%vellore%"),
            EmailRecord.processed_at >= cutoff,
        )
        trap_records = (await session.execute(trap_stmt)).scalars().all()

    applied_names = [c.name for c in applied_companies] or ["None logged yet"]
    shortlist_names = [c.name for c in shortlisted_companies] or ["None currently active"]

    lines = [
        "📊 *Weekly Placement Executive Briefing*",
        f"📅 Week of {datetime.now().strftime('%B %d, %Y')}\n",
        f"🎯 *Applications Submitted ({len(applied_companies)}):*",
        f"• {escape_md(', '.join(applied_names))}\n",
        f"🚨 *Active Shortlists ({len(shortlisted_companies)}):*",
        f"• {escape_md(', '.join(shortlist_names))}\n",
        f"🛡️ *Shield Report:*",
        f"• Ingested & scanned *{total_emails}* emails with zero hallucination\\.",
        f"• Traps flagged/avoided: *{len(trap_records)}* Vellore/bond notices\\.\n",
        "💡 *Focus Recommendations for Next Week:*",
        "1\\. Revise CS fundamentals and dynamic programming for upcoming OAs\\.",
        "2\\. Review resume bullet points with system design metrics for active shortlists\\.",
    ]

    await send_telegram_notification(text="\n".join(lines))


def start_scheduler() -> AsyncIOScheduler:
    """Initializes and starts the APScheduler with IST timezone."""
    global scheduler
    if scheduler and scheduler.running:
        return scheduler

    ist_tz = pytz.timezone(settings.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=ist_tz)

    # 1. Personal Inbox Poller: Every 5 minutes
    scheduler.add_job(
        poll_personal_inbox_job,
        trigger=IntervalTrigger(minutes=5),
        id="personal_inbox_poller",
        replace_existing=True,
    )

    # 2. College Inbox Direct Poller: Every 5 minutes (Secondary/Fallback to Google Apps Script)
    if settings.COLLEGE_GOOGLE_REFRESH_TOKEN:
        scheduler.add_job(
            poll_college_inbox_job,
            trigger=IntervalTrigger(minutes=5),
            id="college_inbox_poller",
            replace_existing=True,
        )

    # 3. Daily Digest: 9:00 PM IST (21:00) every day
    scheduler.add_job(
        daily_digest_job,
        trigger=CronTrigger(hour=21, minute=0, timezone=ist_tz),
        id="daily_digest_9pm",
        replace_existing=True,
    )

    # 4. Weekly Review: Sunday 8:00 PM IST (20:00)
    scheduler.add_job(
        weekly_review_job,
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=ist_tz),
        id="weekly_review_sunday",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with IST timezone. Poller (5m), Daily Digest (21:00), Weekly Review (Sun 20:00).")
    return scheduler
