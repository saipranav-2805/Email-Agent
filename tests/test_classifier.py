from datetime import datetime, timedelta, timezone
import pytest
from app.config import CandidateProfile
from app.services.classifier import (
    ClassificationResult,
    ListTypeEnum,
    TierEnum,
    enforce_safety_rules,
    heuristic_classification,
)
from app.services.parser import AttachmentScanResult


def test_applied_list_safety_logic_never_triggers_tier_1_shortlist(sample_profile):
    """NON-NEGOTIABLE RULE: An 'Applied List' MUST NEVER trigger a Tier 1 Shortlist alert, even if candidate matched!"""
    matched_scan = AttachmentScanResult(
        filename="Oracle_Applied_Students.xlsx",
        matched=True,
        matched_by="reg_no",
        matched_value="21BCE1001",
        sheet_or_page="Sheet1",
        matched_row_dict={"RegNo": "21BCE1001", "Name": "Pranav Sharma"},
    )

    # Simulate an LLM that mistakenly flagged Tier 1
    hallucinated_result = ClassificationResult(
        tier=TierEnum.TIER_1_CRITICAL,
        company_name="Oracle",
        role="Software Developer",
        is_eligible=True,
        list_type=ListTypeEnum.APPLIED_LIST,
        action_required="Check applied list",
        concise_summary="Applied list received with your name.",
    )

    safe_result = enforce_safety_rules(
        result=hallucinated_result,
        subject="Applied List for Oracle Campus Hiring",
        body_text="Please find the attached applied list of registered candidates for Oracle drive.",
        attachment_results=[matched_scan],
        profile=sample_profile,
    )

    # Must be demoted from TIER_1_CRITICAL
    assert safe_result.list_type == ListTypeEnum.APPLIED_LIST
    assert safe_result.tier != TierEnum.TIER_1_CRITICAL
    assert safe_result.tier == TierEnum.TIER_4_NOISE


def test_actual_shortlist_triggers_tier_1_critical(sample_profile):
    """When a shortlist email arrives and candidate is matched in attachment, triggers TIER_1_CRITICAL."""
    matched_scan = AttachmentScanResult(
        filename="Amazon_Shortlisted_Round_2.xlsx",
        matched=True,
        matched_by="reg_no",
        matched_value="21BCE1001",
        sheet_or_page="Round2_Shortlist",
        matched_row_dict={"RegNo": "21BCE1001", "Slot": "11:00 AM"},
    )

    result = heuristic_classification(
        subject="Shortlisted Candidates for Amazon Round 2 Technical Interview",
        body_text="Congratulations! Please find the shortlisted candidates for round 2 technical interview.",
        candidate_matched=True,
        extracted_links=["https://chime.aws/interview123"],
        profile=sample_profile,
    )

    safe_result = enforce_safety_rules(
        result=result,
        subject="Shortlisted Candidates for Amazon Round 2 Technical Interview",
        body_text="Congratulations! Please find the shortlisted candidates for round 2 technical interview.",
        attachment_results=[matched_scan],
        profile=sample_profile,
    )

    assert safe_result.tier == TierEnum.TIER_1_CRITICAL
    assert safe_result.list_type == ListTypeEnum.SHORTLISTED_LIST
    assert safe_result.is_eligible is True


def test_shortlist_negative_candidate_not_shortlisted(sample_profile):
    """If a shortlist email arrives but candidate was NOT matched in attachment, do NOT trigger shortlist alarm."""
    unmatched_scan = AttachmentScanResult(
        filename="Google_Interview_Shortlist.pdf",
        matched=False,
        sheet_or_page="All Pages",
        total_rows_or_pages=5,
    )

    # Even if an LLM assumed shortlist from subject, safety rules must override
    tentative_result = ClassificationResult(
        tier=TierEnum.TIER_1_CRITICAL,
        company_name="Google",
        role="Software Engineer",
        list_type=ListTypeEnum.SHORTLISTED_LIST,
        concise_summary="Google shortlist announced",
    )

    safe_result = enforce_safety_rules(
        result=tentative_result,
        subject="Google Campus Recruitment - Shortlist for Technical Rounds",
        body_text="Attached is the shortlist for tomorrow's technical rounds.",
        attachment_results=[unmatched_scan],
        profile=sample_profile,
    )

    assert safe_result.tier == TierEnum.TIER_4_NOISE
    assert "candidate is NOT in the list" in safe_result.concise_summary


def test_red_flags_detection(sample_profile):
    """Detects Vellore campus travel traps and employment bonds."""
    base_result = ClassificationResult(
        tier=TierEnum.TIER_2_ACTION,
        company_name="Fintech Corp",
        role="Graduate Engineer Trainee",
        ctc="10 LPA",
        is_eligible=True,
        action_required="Apply on Google Form",
    )

    body = (
        "Selected candidates will need to report for offline interview at Vellore campus. "
        "Also requires a 2-year service agreement / bond."
    )

    safe_result = enforce_safety_rules(
        result=base_result,
        subject="Fintech Corp Hiring Drive",
        body_text=body,
        attachment_results=[],
        profile=sample_profile,
    )

    assert any("vellore" in rf.lower() for rf in safe_result.red_flags_detected)
    assert any("service agreement" in rf.lower() for rf in safe_result.red_flags_detected)


def test_urgent_deadline_escalation_to_tier_1(sample_profile):
    """Eligible Tier 2 deadline closing in < 6 hours escalates to TIER_1_CRITICAL."""
    # Set deadline 3 hours from now
    three_hours_later = datetime.now(timezone.utc) + timedelta(hours=3)

    base_result = ClassificationResult(
        tier=TierEnum.TIER_2_ACTION,
        company_name="Stripe",
        role="Backend Engineer",
        is_eligible=True,
        deadline_iso=three_hours_later.isoformat(),
        action_required="Fill NeoPAT Registration",
        concise_summary="NeoPAT registration closes today.",
    )

    safe_result = enforce_safety_rules(
        result=base_result,
        subject="Stripe - NeoPAT Registration Closing Soon",
        body_text="Hurry, NeoPAT form closing in 3 hours.",
        attachment_results=[],
        profile=sample_profile,
    )

    assert safe_result.tier == TierEnum.TIER_1_CRITICAL
    assert "[URGENT" in safe_result.concise_summary
