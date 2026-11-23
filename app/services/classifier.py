import asyncio
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import pytz
from app.config import CandidateProfile, settings
from app.services.parser import AttachmentScanResult

logger = logging.getLogger(__name__)


class TierEnum(str, Enum):
    TIER_1_CRITICAL = "TIER_1_CRITICAL"
    TIER_2_ACTION = "TIER_2_ACTION"
    TIER_3_WORTH_A_LOOK = "TIER_3_WORTH_A_LOOK"
    TIER_4_NOISE = "TIER_4_NOISE"


class ListTypeEnum(str, Enum):
    APPLIED_LIST = "APPLIED_LIST"
    SHORTLISTED_LIST = "SHORTLISTED_LIST"
    INTERVIEW_SCHEDULE = "INTERVIEW_SCHEDULE"
    NONE = "NONE"


class ClassificationResult(BaseModel):
    tier: TierEnum = Field(
        description="Priority tier: TIER_1_CRITICAL, TIER_2_ACTION, TIER_3_WORTH_A_LOOK, or TIER_4_NOISE"
    )
    company_name: Optional[str] = Field(
        default=None, description="Clean company name, e.g., 'Google', 'Microsoft', 'JPMorgan Chase'"
    )
    role: Optional[str] = Field(
        default=None, description="Job title or role name, e.g., 'Software Development Engineer'"
    )
    ctc: Optional[str] = Field(
        default=None, description="Compensation / CTC string, e.g., '18 LPA' or '50k/month internship'"
    )
    is_eligible: bool = Field(
        default=True, description="Whether the candidate satisfies CGPA, branch, and backlog criteria"
    )
    eligibility_reason: str = Field(
        default="", description="Brief explanation of eligibility or reason for ineligibility"
    )
    list_type: ListTypeEnum = Field(
        default=ListTypeEnum.NONE,
        description="Type of attached/referenced list: APPLIED_LIST, SHORTLISTED_LIST, INTERVIEW_SCHEDULE, or NONE"
    )
    action_required: Optional[str] = Field(
        default=None, description="Specific action candidate needs to take, e.g., 'Submit Google Form', 'Take HackerRank test'"
    )
    deadline_iso: Optional[str] = Field(
        default=None, description="ISO 8601 deadline date-time with timezone offset (e.g., '2026-11-24T18:00:00+05:30')"
    )
    links: List[str] = Field(
        default_factory=list, description="Extracted actionable URLs (Google Form, NeoPAT, HackerRank, Mettl, etc.)"
    )
    red_flags_detected: List[str] = Field(
        default_factory=list,
        description="Identified red flags (e.g. 'Offline interview at Vellore', '3-year service agreement', 'Bond')"
    )
    concise_summary: str = Field(
        default="", description="1-2 sentence high-signal summary of the email"
    )
    human_opinion: Optional[str] = Field(
        default=None,
        description="For Tier 3 items: Sharp, candid 1-sentence opinion on whether this webinar/hackathon is genuinely worth time"
    )


def extract_raw_urls(text: str) -> List[str]:
    """Extracts web URLs from email body, prioritizing forms and assessment platforms."""
    url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
    found = re.findall(url_pattern, text)
    cleaned = []
    for u in found:
        u_clean = u.rstrip(".,;)>]").strip()
        if u_clean and u_clean not in cleaned:
            cleaned.append(u_clean)
    return cleaned


def build_system_prompt(profile: CandidateProfile, learned_preferences: List[str]) -> str:
    prefs_text = "\n".join([f"- {p}" for p in learned_preferences]) if learned_preferences else "None currently."

    return f"""You are the sharp, candid Chief of Staff for {profile.name} ({profile.reg_no}).
Your mission: Monitor college placement & personal emails, track campus hiring with zero errors, filter spam, and proactively warn about traps.

CANDIDATE PROFILE:
- Name: {profile.name}
- Registration Number: {profile.reg_no}
- Branch: {profile.branch} ({profile.degree})
- Current CGPA: {profile.cgpa}
- 10th%: {profile.tenth_pct}%, 12th%: {profile.twelfth_pct}%
- Active Backlogs: {profile.active_backlogs}, History of Backlogs: {profile.history_of_backlogs}
- Target Career Interests: {", ".join(profile.career_interests)}
- Disliked Roles / Avoid: {", ".join(profile.disliked_roles)}
- Red Flags: {", ".join(profile.red_flags)}
- Minimum Acceptable CTC: {profile.min_ctc_lpa} LPA
- Preferred Locations: {", ".join(profile.preferred_locations)}

DYNAMIC LEARNED PREFERENCES FROM USER:
{prefs_text}

CLASSIFICATION TIERS:
1. TIER_1_CRITICAL:
   - SHORTLISTED: An attachment or notice shows the candidate is SHORTLISTED for next round / interview / test.
     CRITICAL SAFETY RULE: Only declare shortlisted if the deterministic scan found a match AND the list is a SHORTLIST/INTERVIEW list. An "Applied List" (registered candidates) is NEVER a shortlist!
   - Direct interview invite with time slot.
   - Assessment / Online test link (HackerRank, Mettl, Superset, etc.) addressed to the candidate or company.
   - Actionable eligible deadline (Google Form, NeoPAT) closing in less than 6 hours.

2. TIER_2_ACTION:
   - New eligible company opening or recruitment drive.
   - NeoPAT or campus registration notice with deadline >= 6 hours.
   - Action required from candidate to apply or register.

3. TIER_3_WORTH_A_LOOK:
   - High-value webinars, hackathons, tech talks, off-campus hiring leads matching {profile.name}'s career interests.
   - MUST include a sharp, candid 1-sentence `human_opinion` on whether it's genuinely worth attending or a marketing waste of time.

4. TIER_4_NOISE:
   - Ineligible criteria (CGPA requirement higher than candidate's {profile.cgpa}, branch not eligible, backlogs disallowed).
   - Sales, manual testing, or generic mass marketing blasts.
   - Irrelevant campus circulars (sports day, lost and found, fee reminders).
   - Applied lists where no action is needed and candidate is simply on record.

SAFETY AND ACCURACY DIRECTIVES:
- Distinguish strictly between APPLIED_LIST (students who applied) and SHORTLISTED_LIST (students selected for test/interview). An Applied List is NEVER a Shortlist!
- Red flags detection: Explicitly look for offline travel demands (e.g. "Vellore campus", "offline at Vellore", "travel to Vellore"), bonds, service agreements, training fees, night shifts.
- Be concise, high-signal, zero corporate fluff.
"""


def call_gemini(
    prompt: str,
    system_instruction: str,
    model_name: str = "gemini-2.5-flash",
) -> ClassificationResult:
    """Invokes Gemini using google-genai SDK with strict Pydantic response_schema."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=ClassificationResult,
            temperature=0.1,
        ),
    )

    if not response.text:
        raise ValueError("Empty response received from Gemini.")

    data = json.loads(response.text)
    return ClassificationResult(**data)


def call_groq_fallback(
    prompt: str,
    system_instruction: str,
    model_name: str = "llama-3.3-70b-versatile",
) -> ClassificationResult:
    """Fallback classifier using Groq LLM with JSON mode."""
    from groq import Groq

    client = Groq(api_key=settings.GROQ_API_KEY)

    schema_str = json.dumps(ClassificationResult.model_json_schema(), indent=2)
    augmented_prompt = f"""{prompt}

You must return a valid JSON object strictly complying with this JSON Schema:
{schema_str}

Return ONLY the raw JSON object without markdown formatting or code blocks.
"""

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": augmented_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    raw_text = response.choices[0].message.content
    if not raw_text:
        raise ValueError("Empty response from Groq fallback.")

    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)

    data = json.loads(clean_text)
    return ClassificationResult(**data)


async def classify_email_content(
    subject: str,
    sender: str,
    body_text: str,
    attachment_results: List[AttachmentScanResult],
    profile: CandidateProfile,
    learned_preferences: List[str],
) -> ClassificationResult:
    """Classifies an email with deterministic attachment context, retry backoff, and Groq fallback."""
    system_instruction = build_system_prompt(profile, learned_preferences)

    # Format attachment scan summary
    scan_summaries = []
    candidate_matched_in_any_attachment = False
    for res in attachment_results:
        if res.matched:
            candidate_matched_in_any_attachment = True
            scan_summaries.append(
                f"- File: '{res.filename}' | MATCH FOUND: Candidate matched by {res.matched_by}='{res.matched_value}' "
                f"in {res.sheet_or_page}. Matched row: {res.matched_row_dict}"
            )
        else:
            scan_summaries.append(
                f"- File: '{res.filename}' | NO MATCH. Scanned {res.total_rows_or_pages} rows/pages. Candidate is NOT in this file."
            )

    att_text = "\n".join(scan_summaries) if scan_summaries else "No spreadsheet or PDF attachments present."

    extracted_links = extract_raw_urls(body_text)

    prompt = f"""INCOMING EMAIL FOR TRIAGE:
Sender: {sender}
Subject: {subject}

DETERMINISTIC ATTACHMENT SCAN RESULT:
{att_text}

Raw Extracted URLs:
{extracted_links}

EMAIL BODY:
{body_text[:4000]}
"""

    # Retry loop with exponential backoff and Groq fallback
    result: Optional[ClassificationResult] = None
    last_error = None

    if settings.GEMINI_API_KEY:
        candidate_models = [settings.GEMINI_FAST_MODEL, "gemini-3.5-flash-lite", "gemini-3.8-flash"]
        unique_models = [m for i, m in enumerate(candidate_models) if m and m not in candidate_models[:i]]

        for model in unique_models:
            for attempt in range(2):
                try:
                    result = await asyncio.to_thread(
                        call_gemini,
                        prompt,
                        system_instruction,
                        model,
                    )
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(f"Gemini ({model}) attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(1)
            if result is not None:
                break

    # If Gemini failed or no Gemini API key, fallback to Groq
    if result is None and settings.GROQ_API_KEY:
        logger.info("Attempting classification via Groq fallback...")
        groq_models = [settings.GROQ_MODEL, "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
        unique_groq = [m for i, m in enumerate(groq_models) if m and m not in groq_models[:i]]
        for g_model in unique_groq:
            try:
                result = await asyncio.to_thread(
                    call_groq_fallback,
                    prompt,
                    system_instruction,
                    g_model,
                )
                if result is not None:
                    break
            except Exception as e:
                logger.error(f"Groq fallback ({g_model}) failed: {e}")
                last_error = e

    # If both LLMs are unavailable, fallback to deterministic heuristic classifier
    if result is None:
        logger.warning(f"All LLMs unavailable ({last_error}). Using deterministic heuristic fallback.")
        result = heuristic_classification(
            subject=subject,
            body_text=body_text,
            candidate_matched=candidate_matched_in_any_attachment,
            extracted_links=extracted_links,
            profile=profile,
        )

    # Merge extracted links if LLM missed them
    for url in extracted_links:
        if url not in result.links:
            result.links.append(url)

    # POST-PROCESSING SAFETY ENFORCEMENT
    result = enforce_safety_rules(
        result=result,
        subject=subject,
        body_text=body_text,
        attachment_results=attachment_results,
        profile=profile,
    )

    return result


def enforce_safety_rules(
    result: ClassificationResult,
    subject: str,
    body_text: str,
    attachment_results: List[AttachmentScanResult],
    profile: CandidateProfile,
) -> ClassificationResult:
    """Enforces deterministic post-processing rules to prevent false positives and missed deadlines."""
    combined_text = f"{subject} {body_text}".lower()

    # Rule 1: Applied List vs Shortlist Safety Logic
    # An applied list is NEVER a shortlist!
    is_applied_list = (
        "applied list" in combined_text
        or "registered students" in combined_text
        or "list of applied" in combined_text
        or "willingness list" in combined_text
        or "registered candidates" in combined_text
    )
    if is_applied_list:
        result.list_type = ListTypeEnum.APPLIED_LIST

    # Check if attachment matched
    any_matched = any(res.matched for res in attachment_results)
    any_attachments = len(attachment_results) > 0

    if result.list_type == ListTypeEnum.APPLIED_LIST:
        # Applied lists must NEVER trigger a Tier 1 Shortlist alert
        if result.tier == TierEnum.TIER_1_CRITICAL:
            result.tier = TierEnum.TIER_4_NOISE
            result.concise_summary += " (Applied list on record; no immediate test/interview scheduled)."

    # If attachment was scanned but candidate NOT found, candidate is NOT shortlisted
    if any_attachments and not any_matched:
        if "shortlist" in combined_text or "interview" in combined_text:
            if result.tier == TierEnum.TIER_1_CRITICAL:
                result.tier = TierEnum.TIER_4_NOISE
                result.concise_summary = "Shortlist/schedule released, but candidate is NOT in the list."

    # Rule 2: Red Flags Detection Check (Vellore travel, bonds)
    for flag in profile.red_flags:
        flag_lower = flag.lower()
        if flag_lower in combined_text:
            if flag not in result.red_flags_detected:
                result.red_flags_detected.append(flag)

    # Rule 3: Urgent Deadline Escalation (< 6 hours)
    if result.deadline_iso and result.is_eligible and result.tier == TierEnum.TIER_2_ACTION:
        try:
            # Parse ISO deadline
            clean_iso = result.deadline_iso.replace("Z", "+00:00")
            dl_dt = datetime.fromisoformat(clean_iso)
            now_dt = datetime.now(timezone.utc)
            if dl_dt.tzinfo is None:
                # Assume IST if naive
                ist = pytz.timezone(settings.TIMEZONE)
                dl_dt = ist.localize(dl_dt)

            hours_left = (dl_dt - now_dt).total_seconds() / 3600.0
            if 0 < hours_left <= 6.0:
                result.tier = TierEnum.TIER_1_CRITICAL
                result.concise_summary = f"[URGENT - {hours_left:.1f}h left] " + result.concise_summary
        except Exception as e:
            logger.debug(f"Could not parse deadline '{result.deadline_iso}' for time check: {e}")

    return result


def heuristic_classification(
    subject: str,
    body_text: str,
    candidate_matched: bool,
    extracted_links: List[str],
    profile: CandidateProfile,
) -> ClassificationResult:
    """Deterministic fallback classification when external LLMs are unreachable."""
    text_lower = f"{subject} {body_text}".lower()

    # Red flags
    flags = [rf for rf in profile.red_flags if rf.lower() in text_lower]

    # Check for Applied list
    if "applied list" in text_lower or "registered candidates" in text_lower:
        return ClassificationResult(
            tier=TierEnum.TIER_4_NOISE,
            list_type=ListTypeEnum.APPLIED_LIST,
            is_eligible=True,
            eligibility_reason="Applied list notification",
            concise_summary=f"Applied list published for {subject[:50]}",
            red_flags_detected=flags,
            links=extracted_links,
        )

    # Shortlist check
    if candidate_matched and ("shortlist" in text_lower or "interview" in text_lower or "test" in text_lower):
        return ClassificationResult(
            tier=TierEnum.TIER_1_CRITICAL,
            list_type=ListTypeEnum.SHORTLISTED_LIST,
            is_eligible=True,
            eligibility_reason="Candidate matched in shortlist/interview attachment",
            action_required="Check interview/test schedule details immediately",
            concise_summary=f"Shortlisted for {subject[:60]}",
            red_flags_detected=flags,
            links=extracted_links,
        )

    # Test link or assessment
    for test_kw in ["hackerrank", "mettl", "superset", "neopat", "test link", "assessment link"]:
        if test_kw in text_lower:
            return ClassificationResult(
                tier=TierEnum.TIER_1_CRITICAL,
                is_eligible=True,
                eligibility_reason="Online assessment link received",
                action_required="Take online test before deadline",
                concise_summary=f"Assessment / Test link for {subject[:50]}",
                red_flags_detected=flags,
                links=extracted_links,
            )

    # New opening / Tier 2
    if any(k in text_lower for k in ["recruitment", "hiring", "job description", "placement drive", "form"]):
        return ClassificationResult(
            tier=TierEnum.TIER_2_ACTION,
            is_eligible=True,
            eligibility_reason="Campus hiring announcement",
            action_required="Review eligibility & apply via link/form",
            concise_summary=subject[:80],
            red_flags_detected=flags,
            links=extracted_links,
        )

    # Webinar / Tier 3
    if any(k in text_lower for k in ["webinar", "hackathon", "workshop", "tech talk", "bootcamp"]):
        return ClassificationResult(
            tier=TierEnum.TIER_3_WORTH_A_LOOK,
            is_eligible=True,
            eligibility_reason="Educational / Tech session",
            concise_summary=subject[:80],
            human_opinion="Check if the session topic directly aligns with your tech stack before spending an hour.",
            links=extracted_links,
        )

    return ClassificationResult(
        tier=TierEnum.TIER_4_NOISE,
        is_eligible=False,
        eligibility_reason="General update / noise",
        concise_summary=subject[:80],
    )
