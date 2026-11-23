import asyncio
import base64
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import or_, select
from app.bot.telegram_app import send_telegram_notification
from app.bot.ui import format_telegram_card
from app.config import load_candidate_profile, settings
from app.db.models import Company, EmailRecord, UserPreference
from app.db.session import get_db_session
from app.services.calendar_service import create_calendar_event
from app.services.classifier import (
    ClassificationResult,
    ListTypeEnum,
    TierEnum,
    classify_email_content,
)
from app.services.gmail_service import send_placement_loud_alarm
from app.services.parser import AttachmentScanResult, scan_attachment

logger = logging.getLogger(__name__)


async def get_active_learned_preferences() -> List[str]:
    """Retrieves all active dynamic user preferences from database."""
    async with get_db_session() as session:
        stmt = select(UserPreference.rule_text).where(UserPreference.is_active == True)
        rules = (await session.execute(stmt)).scalars().all()
        return list(rules)


async def find_or_link_company(
    company_name: Optional[str],
    role: Optional[str] = None,
    ctc: Optional[str] = None,
    subject: str = "",
) -> Optional[Company]:
    """Cross-inbox memory: finds an existing company record by name/alias or registers a new one."""
    search_term = company_name.strip() if company_name else ""
    if not search_term or len(search_term) < 2:
        return None

    async with get_db_session() as session:
        # 1. Exact or case-insensitive search
        stmt = select(Company).where(
            or_(
                Company.name.ilike(search_term),
                Company.aliases.ilike(f"%{search_term}%"),
            )
        )
        company = (await session.execute(stmt)).scalars().first()

        if company:
            # Update role or ctc if previously blank
            updated = False
            if role and not company.role:
                company.role = role
                updated = True
            if ctc and not company.ctc:
                company.ctc = ctc
                updated = True
            if updated:
                company.updated_at = datetime.now(timezone.utc)
                await session.commit()
            return company

        # 2. Create new company record in pipeline
        new_company = Company(
            name=search_term,
            role=role,
            ctc=ctc,
            status="interested",
            aliases=json.dumps([search_term]),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(new_company)
        await session.commit()
        await session.refresh(new_company)
        return new_company


async def process_incoming_email(
    source_inbox: str,
    message_id: str,
    thread_id: str,
    sender: str,
    subject: str,
    body_text: str,
    received_at: datetime,
    raw_attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Main ingestion pipeline: deduplication, deterministic attachment scan, 4-tier triage, notifications, calendar sync, and alarm."""
    # Circuit breaker: Never ingest or process agent's own alarm emails
    if "[placement-alarm]" in subject.lower() or "chief of staff priority alarm" in body_text.lower():
        logger.info(f"Dropping alarm feedback email to prevent feedback loop: {subject}")
        return {
            "status": "dropped",
            "message": "Placement alarm loop prevented",
        }

    profile = load_candidate_profile()

    # 1. Deduplication Check
    async with get_db_session() as session:
        stmt = select(EmailRecord).where(
            EmailRecord.source_inbox == source_inbox,
            EmailRecord.message_id == message_id,
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            logger.info(f"Skipping duplicate email [{source_inbox}] ID: {message_id}")
            return {
                "status": "duplicate",
                "email_id": existing.id,
                "tier": existing.tier,
                "message": "Already processed",
            }

    # 2. Deterministic Attachment Parsing
    attachment_results: List[AttachmentScanResult] = []
    if raw_attachments:
        for att in raw_attachments:
            fn = att.get("filename", "unknown")
            b64_content = att.get("content_base64", "")
            if not b64_content:
                continue

            try:
                content_bytes = base64.b64decode(b64_content)
                scan_res = scan_attachment(
                    filename=fn,
                    file_bytes=content_bytes,
                    profile=profile.model_dump(),
                )
                attachment_results.append(scan_res)
                logger.info(f"Attachment scan for '{fn}': Matched={scan_res.matched} ({scan_res.matched_by})")
            except Exception as e:
                logger.error(f"Failed to decode or parse attachment '{fn}': {e}")
                attachment_results.append(
                    AttachmentScanResult(filename=fn, matched=False, error=str(e))
                )

    # 3. Dynamic Preferences & 4-Tier Classification
    learned_prefs = await get_active_learned_preferences()
    classification: ClassificationResult = await classify_email_content(
        subject=subject,
        sender=sender,
        body_text=body_text,
        attachment_results=attachment_results,
        profile=profile,
        learned_preferences=learned_prefs,
    )

    # 4. Cross-Inbox Company Linking
    company_obj = await find_or_link_company(
        company_name=classification.company_name,
        role=classification.role,
        ctc=classification.ctc,
        subject=subject,
    )
    company_id = company_obj.id if company_obj else None

    # If shortlisted or interview list, update company status
    if company_obj and classification.list_type == ListTypeEnum.SHORTLISTED_LIST:
        async with get_db_session() as session:
            c = await session.get(Company, company_obj.id)
            if c and c.status not in ["offer"]:
                c.status = "shortlisted"
                c.updated_at = datetime.now(timezone.utc)
                await session.commit()

    # 5. Calendar Event Creation (For Tier 1 & Tier 2)
    calendar_event_id = None
    if classification.tier in [TierEnum.TIER_1_CRITICAL, TierEnum.TIER_2_ACTION] and classification.deadline_iso:
        try:
            event_title = f"[Placement] {classification.company_name or 'Urgent'} - {classification.action_required or 'Deadline'}"
            desc = (
                f"Role: {classification.role or 'N/A'}\n"
                f"CTC: {classification.ctc or 'N/A'}\n"
                f"Action: {classification.action_required or 'Review'}\n"
                f"Links: {', '.join(classification.links)}"
            )
            cal_res = await asyncio.to_thread(
                create_calendar_event,
                title=event_title,
                start_iso=classification.deadline_iso,
                duration_mins=60,
                description=desc,
            )
            if cal_res:
                calendar_event_id = cal_res.get("event_id")
        except Exception as e:
            logger.error(f"Failed auto-calendar event creation: {e}")

    # 6. Free Loud Alarm Bridge (For TIER_1_CRITICAL)
    alarm_sent = False
    clean_comp = (classification.company_name or "").strip()
    is_valid_company = clean_comp and clean_comp.lower() not in ["none", "unknown", "(no subject)", "not specified", "campus opportunity"]

    if classification.tier == TierEnum.TIER_1_CRITICAL and is_valid_company:
        try:
            alarm_sent = await asyncio.to_thread(
                send_placement_loud_alarm,
                company_name=clean_comp,
                reason=classification.action_required or "Shortlist / Urgent Deadline",
                action_details=classification.concise_summary,
            )
        except Exception as e:
            logger.error(f"Failed loud alarm dispatch: {e}")

    # 7. Persist to Database
    async with get_db_session() as session:
        email_record = EmailRecord(
            source_inbox=source_inbox,
            message_id=message_id,
            thread_id=thread_id,
            sender=sender,
            subject=subject,
            body_text=body_text,
            received_at=received_at,
            processed_at=datetime.now(timezone.utc),
            tier=classification.tier.value,
            classification_json=json.dumps(classification.model_dump()),
            attachment_scan_json=json.dumps([r.model_dump() for r in attachment_results]),
            calendar_event_id=calendar_event_id,
            alarm_sent=alarm_sent,
            company_id=company_id,
        )
        session.add(email_record)
        await session.commit()
        await session.refresh(email_record)
        created_email_id = email_record.id

    # 8. Notifications to Telegram (Only Tier 1 & Tier 2 buzz immediately)
    telegram_msg_id = None
    if classification.tier in [TierEnum.TIER_1_CRITICAL, TierEnum.TIER_2_ACTION]:
        msg_text, reply_markup = format_telegram_card(
            classification=classification,
            source_inbox=source_inbox,
            email_id=created_email_id,
            company_id=company_id,
        )
        telegram_msg_id = await send_telegram_notification(
            text=msg_text,
            reply_markup=reply_markup,
        )

        if telegram_msg_id:
            async with get_db_session() as session:
                rec = await session.get(EmailRecord, created_email_id)
                if rec:
                    rec.telegram_message_id = telegram_msg_id
                    await session.commit()

    return {
        "status": "processed",
        "email_id": created_email_id,
        "tier": classification.tier.value,
        "company": classification.company_name,
        "calendar_event_id": calendar_event_id,
        "alarm_sent": alarm_sent,
        "telegram_message_id": telegram_msg_id,
    }
