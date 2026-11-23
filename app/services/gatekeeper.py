import logging
import re
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel

logger = logging.getLogger(__name__)

HIGH_SIGNAL_KEYWORDS = [
    "assessment",
    "interview",
    "shortlist",
    "shortlisted",
    "hackerrank",
    "mettl",
    "superset",
    "neopat",
    "offer",
    "application",
    "test link",
    "coding challenge",
    "coding test",
    "online assessment",
    "oa link",
    "technical test",
    "selection process",
    "hiring process",
    "code pair",
    "technical round",
]


class GatekeeperDecision(BaseModel):
    is_allowed: bool
    reason: str
    matched_keyword: Optional[str] = None
    matched_company: Optional[str] = None


def _contains_keyword(text: str, keyword: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])"
    return bool(re.search(pattern, text.lower()))


def evaluate_personal_email(
    sender: str,
    subject: str,
    labels: List[str],
    headers: Dict[str, str],
    known_companies: List[str],
    body_snippet: str = "",
) -> GatekeeperDecision:
    """Evaluates whether an email from Personal Gmail passes the Zero-AI Spam Gatekeeper.

    Rule: Skip emails in CATEGORY_PROMOTIONS or CATEGORY_SOCIAL or containing List-Unsubscribe
    UNLESS sender/subject matches a company in DB OR contains high-signal keywords.
    """
    subject_clean = (subject or "").strip()
    sender_clean = (sender or "").strip()
    combined_header_text = f"{subject_clean} {sender_clean}".lower()
    snippet_clean = (body_snippet or "")[:500].lower()

    matched_company = None
    matched_keyword = None

    # Check for Known Tracked Companies
    for company in known_companies:
        comp_clean = (company or "").strip().lower()
        if len(comp_clean) >= 3:
            if _contains_keyword(combined_header_text, comp_clean):
                matched_company = company
                break

    # Check for High-Signal Keywords in Subject or Sender
    for kw in HIGH_SIGNAL_KEYWORDS:
        if _contains_keyword(combined_header_text, kw):
            matched_keyword = kw
            break

    # Check snippet for critical test links or assessments if not found in headers
    if not matched_keyword:
        for kw in ["assessment", "hackerrank", "mettl", "test link", "oa link"]:
            if _contains_keyword(snippet_clean, kw):
                matched_keyword = kw
                break

    if matched_company and matched_keyword:
        return GatekeeperDecision(
            is_allowed=True,
            reason=f"Matched tracked company '{matched_company}' and high-signal keyword '{matched_keyword}'",
            matched_company=matched_company,
            matched_keyword=matched_keyword,
        )
    elif matched_company:
        return GatekeeperDecision(
            is_allowed=True,
            reason=f"Matched tracked company '{matched_company}' in headers",
            matched_company=matched_company,
        )
    elif matched_keyword:
        return GatekeeperDecision(
            is_allowed=True,
            reason=f"Matched high-signal keyword '{matched_keyword}'",
            matched_keyword=matched_keyword,
        )

    # 3. Check for Promotional or Social Category Labels
    upper_labels = [lbl.upper() for lbl in (labels or [])]
    if "CATEGORY_PROMOTIONS" in upper_labels:
        return GatekeeperDecision(
            is_allowed=False,
            reason="Blocked: CATEGORY_PROMOTIONS without company match or high-signal keyword",
        )
    if "CATEGORY_SOCIAL" in upper_labels:
        return GatekeeperDecision(
            is_allowed=False,
            reason="Blocked: CATEGORY_SOCIAL without company match or high-signal keyword",
        )

    # 4. Check for List-Unsubscribe Header (Newsletters, automated blasts)
    # Case-insensitive header lookup
    normalized_headers = {k.lower(): v for k, v in (headers or {}).items()}
    if "list-unsubscribe" in normalized_headers or "list-unsubscribe-post" in normalized_headers:
        return GatekeeperDecision(
            is_allowed=False,
            reason="Blocked: List-Unsubscribe header present without company match or high-signal keyword",
        )

    # 5. Direct 1-on-1 personal email without spam markers
    return GatekeeperDecision(
        is_allowed=True,
        reason="Allowed: Direct personal email (no spam markers detected)",
    )
