import base64
from email.mime.text import MIMEText
import logging
import re
from typing import Any, Dict, List, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def get_google_credentials() -> Optional[Credentials]:
    """Builds Google OAuth2 credentials using client ID, secret, and refresh token."""
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REFRESH_TOKEN):
        return None

    try:
        creds = Credentials(
            token=None,
            refresh_token=settings.GOOGLE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=SCOPES,
        )
        return creds
    except Exception as e:
        logger.error(f"Failed to create Google OAuth credentials: {e}")
        return None


def get_gmail_service():
    """Builds authorized Gmail API service."""
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error(f"Failed to build Gmail service: {e}")
        return None


def _extract_body_from_payload(payload: Dict[str, Any]) -> str:
    """Recursively traverses Gmail payload parts to extract plain text or HTML."""
    if not payload:
        return ""

    body = payload.get("body", {})
    data = body.get("data")
    if data:
        try:
            return base64.urlsafe_b64decode(data.encode("ASCII")).decode("utf-8", errors="replace")
        except Exception:
            pass

    parts = payload.get("parts", [])
    text_content = ""
    for part in parts:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/plain":
            part_body = part.get("body", {})
            part_data = part_body.get("data")
            if part_data:
                try:
                    return base64.urlsafe_b64decode(part_data.encode("ASCII")).decode("utf-8", errors="replace")
                except Exception:
                    pass
        elif mime_type == "text/html" and not text_content:
            part_body = part.get("body", {})
            part_data = part_body.get("data")
            if part_data:
                try:
                    raw_html = base64.urlsafe_b64decode(part_data.encode("ASCII")).decode("utf-8", errors="replace")
                    text_content = re.sub(r"<[^>]+>", " ", raw_html)
                except Exception:
                    pass
        elif part.get("parts"):
            nested = _extract_body_from_payload(part)
            if nested:
                return nested

    return text_content


def fetch_recent_personal_emails(max_results: int = 15) -> List[Dict[str, Any]]:
    """Fetches recent personal emails using Gmail API for gatekeeper evaluation."""
    service = get_gmail_service()
    if not service:
        logger.debug("Gmail service not configured or authenticated.")
        return []

    try:
        # Fetch recent messages from inbox
        results = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="in:inbox",
        ).execute()

        messages = results.get("messages", [])
        detailed_emails = []

        for m in messages:
            msg_id = m.get("id")
            full_msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()

            payload = full_msg.get("payload", {})
            headers_list = payload.get("headers", [])
            headers = {h["name"]: h["value"] for h in headers_list if "name" in h and "value" in h}

            subject = headers.get("Subject", "(No Subject)")
            sender = headers.get("From", "")
            received_at = headers.get("Date", "")
            labels = full_msg.get("labelIds", [])
            snippet = full_msg.get("snippet", "")
            body_text = _extract_body_from_payload(payload) or snippet

            detailed_emails.append({
                "message_id": msg_id,
                "thread_id": full_msg.get("threadId", msg_id),
                "sender": sender,
                "subject": subject,
                "body_text": body_text,
                "snippet": snippet,
                "received_at": received_at,
                "labels": labels,
                "headers": headers,
            })

        return detailed_emails

    except Exception as e:
        logger.error(f"Error fetching personal emails from Gmail API: {e}")
        return []


def send_placement_loud_alarm(company_name: str, reason: str, action_details: str) -> bool:
    """Sends a priority email to personal Gmail with [PLACEMENT-ALARM] to trigger iOS Shortcuts loud alarm."""
    service = get_gmail_service()
    recipient = settings.PERSONAL_GMAIL_ADDRESS
    if not service or not recipient:
        logger.warning("Cannot send loud alarm email: Gmail service or PERSONAL_GMAIL_ADDRESS not configured.")
        return False

    try:
        subject = f"[PLACEMENT-ALARM] {company_name or 'Urgent Placement'} - {reason}"
        body = (
            f"CHIEF OF STAFF PRIORITY ALARM:\n\n"
            f"Company: {company_name}\n"
            f"Reason: {reason}\n"
            f"Action Required: {action_details}\n\n"
            f"Wake up and check this immediately!"
        )

        message = MIMEText(body)
        message["to"] = recipient
        message["from"] = recipient
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ASCII")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Successfully dispatched [PLACEMENT-ALARM] email to {recipient} for {company_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch [PLACEMENT-ALARM] email: {e}")
        return False


def search_personal_gmail(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Live search across Personal Gmail messages."""
    service = get_gmail_service()
    if not service:
        return []

    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages = results.get("messages", [])
        found_items = []
        for m in messages:
            msg = service.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            found_items.append({
                "message_id": m["id"],
                "subject": headers.get("Subject", ""),
                "sender": headers.get("From", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })
        return found_items
    except Exception as e:
        logger.error(f"Failed to search personal Gmail: {e}")
        return []
