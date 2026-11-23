from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.bot.telegram_app import init_telegram_webhook, process_telegram_update
from app.config import settings
from app.db.session import engine, get_db_session, init_db
from app.services.processor import process_incoming_email
from app.services.scheduler import get_scheduler, start_scheduler

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------

class AttachmentPayload(BaseModel):
    filename: str
    mime_type: Optional[str] = "application/octet-stream"
    content_base64: str


class CollegeWebhookPayload(BaseModel):
    message_id: str
    thread_id: Optional[str] = None
    sender: str
    subject: str
    body_text: str
    received_at: Optional[str] = None
    attachments: Optional[List[AttachmentPayload]] = Field(default_factory=list)


# ---------------------------------------------------------
# Lifespan Management
# ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Placement & Inbox Chief of Staff backend...")
    await init_db()
    await init_telegram_webhook()
    start_scheduler()
    logger.info("Application startup complete.")

    yield

    # Shutdown
    logger.info("Shutting down application...")
    sched = get_scheduler()
    if sched and sched.running:
        sched.shutdown(wait=False)
    await engine.dispose()
    logger.info("Application shutdown clean.")


app = FastAPI(
    title="Placement & Inbox Chief of Staff",
    description="Dual-Inbox AI Assistant for Campus Hiring & Personal Mail Management",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "app": "Placement & Inbox Chief of Staff",
        "status": "operational",
        "timezone": settings.TIMEZONE,
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """Keep-alive ping endpoint for cron-job.org or monitoring systems."""
    db_status = "connected"
    try:
        async with get_db_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        db_status = "error"

    sched = get_scheduler()
    scheduler_status = "running" if (sched and sched.running) else "stopped"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "scheduler": scheduler_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/ingest/college")
async def ingest_college_email(
    payload: CollegeWebhookPayload,
    x_api_key: Optional[str] = Header(None, alias="X-API-KEY"),
):
    """Secured webhook endpoint receiving college emails forwarded from Google Apps Script."""
    if not x_api_key or x_api_key != settings.COLLEGE_WEBHOOK_API_KEY:
        logger.warning(f"Unauthorized /ingest/college attempt with key: '{x_api_key}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-KEY header",
        )

    # Parse date
    received_dt = datetime.now(timezone.utc)
    if payload.received_at:
        try:
            clean_date = payload.received_at.replace("Z", "+00:00")
            received_dt = datetime.fromisoformat(clean_date)
        except Exception:
            pass

    # Convert attachments
    raw_atts = [
        {
            "filename": att.filename,
            "mime_type": att.mime_type,
            "content_base64": att.content_base64,
        }
        for att in (payload.attachments or [])
    ]

    result = await process_incoming_email(
        source_inbox="COLLEGE",
        message_id=payload.message_id,
        thread_id=payload.thread_id or payload.message_id,
        sender=payload.sender,
        subject=payload.subject,
        body_text=payload.body_text,
        received_at=received_dt,
        raw_attachments=raw_atts,
    )

    return result


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    """Secured Telegram Webhook endpoint."""
    if settings.TELEGRAM_WEBHOOK_SECRET:
        if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
            logger.warning("Unauthorized Telegram webhook call (secret token mismatch)")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret")

    data = await request.json()
    await process_telegram_update(data)
    return {"ok": True}
