from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from googleapiclient.discovery import build
import pytz
from app.config import settings
from app.services.gmail_service import get_google_credentials

logger = logging.getLogger(__name__)


def get_calendar_service():
    """Builds authorized Google Calendar API service."""
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"Failed to build Calendar service: {e}")
        return None


def create_calendar_event(
    title: str,
    start_iso: str,
    duration_mins: int = 60,
    description: str = "",
    location: str = "",
) -> Optional[dict]:
    """Creates a Google Calendar event with 2-hour and 30-minute pop-up reminders.

    Returns dict with {"event_id": str, "html_link": str} on success, or None on failure.
    """
    service = get_calendar_service()
    if not service:
        logger.warning("Google Calendar service not configured or authenticated.")
        return None

    try:
        # Parse start datetime
        clean_iso = start_iso.replace("Z", "+00:00")
        try:
            start_dt = datetime.fromisoformat(clean_iso)
        except Exception:
            logger.warning(f"Invalid ISO datetime string '{start_iso}'. Using current time + 1 day.")
            start_dt = datetime.now(timezone.utc) + timedelta(days=1)

        if start_dt.tzinfo is None:
            ist = pytz.timezone(settings.TIMEZONE)
            start_dt = ist.localize(start_dt)

        end_dt = start_dt + timedelta(minutes=duration_mins)

        event_body = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": settings.TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": settings.TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 120},  # 2 hours before
                    {"method": "popup", "minutes": 30},   # 30 minutes before
                ],
            },
        }

        created_event = service.events().insert(
            calendarId="primary",
            body=event_body,
        ).execute()

        event_id = created_event.get("id")
        html_link = created_event.get("htmlLink", "")
        logger.info(f"Successfully created Google Calendar event '{title}' (ID: {event_id})")
        return {"event_id": event_id, "html_link": html_link}

    except Exception as e:
        logger.error(f"Failed to create Google Calendar event '{title}': {e}")
        return None
