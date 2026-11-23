from datetime import datetime, timezone
import pytest
from sqlalchemy import func, select
from app.db.models import EmailRecord
from app.db.session import get_db_session, init_db
from app.services.processor import process_incoming_email


@pytest.mark.asyncio
async def test_email_deduplication_by_message_id():
    """Verifies that duplicate emails with identical (source_inbox, message_id) are dropped silently."""
    await init_db()

    msg_id = "test_msg_unique_999888"
    thread_id = "thread_unique_999888"
    sender = "cdc@college.edu"
    subject = "Campus Hiring Drive: Atlassian"
    body = "Atlassian is hiring for SDE role. CTC 30 LPA. Fill form by tomorrow."
    now_dt = datetime.now(timezone.utc)

    # First ingestion: should process
    res1 = await process_incoming_email(
        source_inbox="COLLEGE",
        message_id=msg_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        body_text=body,
        received_at=now_dt,
        raw_attachments=[],
    )

    assert res1["status"] == "processed"
    assert res1["email_id"] is not None

    # Second ingestion: should detect duplicate
    res2 = await process_incoming_email(
        source_inbox="COLLEGE",
        message_id=msg_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        body_text=body,
        received_at=now_dt,
        raw_attachments=[],
    )

    assert res2["status"] == "duplicate"
    assert res2["email_id"] == res1["email_id"]

    # Verify database has exactly 1 record
    async with get_db_session() as session:
        stmt = select(func.count(EmailRecord.id)).where(
            EmailRecord.source_inbox == "COLLEGE",
            EmailRecord.message_id == msg_id,
        )
        count = (await session.execute(stmt)).scalar()
        assert count == 1
