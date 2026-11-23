from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from app.config import settings
from app.db.session import init_db
from app.main import app


@pytest.mark.asyncio
async def test_health_check_endpoint():
    """Tests GET /health returns 200 with status, db, and scheduler fields."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ok", "degraded"]
        assert "db" in data
        assert "scheduler" in data


@pytest.mark.asyncio
async def test_college_ingest_auth_failure():
    """Tests POST /ingest/college rejects requests with missing or invalid X-API-KEY."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "message_id": "msg_unauthorized_123",
            "sender": "cdc@college.edu",
            "subject": "Placement Notice",
            "body_text": "Sample text",
        }

        # Missing header
        res1 = await client.post("/ingest/college", json=payload)
        assert res1.status_code == 401

        # Wrong key
        res2 = await client.post(
            "/ingest/college",
            json=payload,
            headers={"X-API-KEY": "wrong_key_xyz"},
        )
        assert res2.status_code == 401


@pytest.mark.asyncio
async def test_college_ingest_successful_flow():
    """Tests POST /ingest/college successfully ingests email when authorized."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "message_id": "msg_api_success_456",
            "thread_id": "thread_api_success_456",
            "sender": "cdc@college.edu",
            "subject": "Urgent: Uber Campus Recruitment Drive - SDE Intern",
            "body_text": "Uber is hiring SDE Interns. Stipend 1.5L/month. Apply before tomorrow.",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "attachments": [],
        }

        response = await client.post(
            "/ingest/college",
            json=payload,
            headers={"X-API-KEY": settings.COLLEGE_WEBHOOK_API_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["email_id"] is not None
