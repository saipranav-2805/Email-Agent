import json
import pytest
from app.db.models import Company, EmailRecord, UserPreference
from app.db.session import get_db_session
from app.services.chat_agent import (
    tool_get_company_pipeline,
    tool_save_preference,
    tool_search_emails,
    tool_update_company_status,
)


@pytest.mark.asyncio
async def test_chat_agent_pipeline_and_status_update():
    """Tests updating company status and viewing the pipeline."""
    # 1. Update company status to applied
    msg = await tool_update_company_status("Uber", "applied", "Filled Google form")
    assert "Uber" in msg
    assert "applied" in msg

    # 2. Query pipeline
    pipeline_json = await tool_get_company_pipeline("applied")
    data = json.loads(pipeline_json)
    assert len(data) == 1
    assert data[0]["name"] == "Uber"
    assert data[0]["status"] == "applied"


@pytest.mark.asyncio
async def test_chat_agent_save_preference():
    """Tests persisting dynamic learned preference rules."""
    rule = "Ignore mechanical engineering core roles and sales vacancies"
    res = await tool_save_preference(rule)
    assert "Saved permanent rule" in res

    async with get_db_session() as session:
        from sqlalchemy import select
        stmt = select(UserPreference).where(UserPreference.rule_text == rule)
        pref = (await session.execute(stmt)).scalars().first()
        assert pref is not None
        assert pref.is_active is True


@pytest.mark.asyncio
async def test_chat_agent_search_emails():
    """Tests email search tool across database."""
    from datetime import datetime, timezone

    async with get_db_session() as session:
        rec = EmailRecord(
            source_inbox="COLLEGE",
            message_id="msg_search_test_123",
            thread_id="thread_search_test_123",
            sender="cdc@college.edu",
            subject="Special HackerRank Coding Link for Goldman Sachs",
            body_text="Your online assessment is active on HackerRank.",
            received_at=datetime.now(timezone.utc),
            processed_at=datetime.now(timezone.utc),
            tier="TIER_1_CRITICAL",
        )
        session.add(rec)
        await session.commit()

    search_res = await tool_search_emails("HackerRank", "COLLEGE")
    results = json.loads(search_res)
    assert len(results) >= 1
    assert "Goldman Sachs" in results[0]["subject"]
