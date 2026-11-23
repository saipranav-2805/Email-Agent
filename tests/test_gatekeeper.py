import pytest
from app.services.gatekeeper import evaluate_personal_email


def test_promotional_email_without_signal_is_blocked():
    """Generic promotional blast is blocked by Zero-AI Gatekeeper."""
    decision = evaluate_personal_email(
        sender="deals@swiggy.com",
        subject="50% off on your next biryani order!",
        labels=["CATEGORY_PROMOTIONS", "INBOX"],
        headers={},
        known_companies=["Google", "Microsoft"],
        body_snippet="Hungry? Order now from your favorite restaurants.",
    )

    assert decision.is_allowed is False
    assert "CATEGORY_PROMOTIONS" in decision.reason


def test_promotional_email_with_high_signal_keyword_is_allowed():
    """Promotional label overridden by high-signal keyword (e.g. HackerRank assessment)."""
    decision = evaluate_personal_email(
        sender="notifications@hackerrank.net",
        subject="Your Coding Assessment for Goldman Sachs is ready",
        labels=["CATEGORY_PROMOTIONS", "INBOX"],
        headers={"List-Unsubscribe": "<https://hackerrank.net/unsub>"},
        known_companies=["Microsoft"],
        body_snippet="Click here to start your 90-minute online test link.",
    )

    assert decision.is_allowed is True
    assert decision.matched_keyword in ["assessment", "coding", "test link"]


def test_promotional_email_matching_known_company_is_allowed():
    """Promotional label overridden because sender/subject matches a tracked company in DB."""
    decision = evaluate_personal_email(
        sender="talent@oracle.com",
        subject="Oracle Engineering Application Update",
        labels=["CATEGORY_PROMOTIONS", "INBOX"],
        headers={},
        known_companies=["Oracle", "Amazon", "Cisco"],
        body_snippet="Thank you for your interest in Oracle.",
    )

    assert decision.is_allowed is True
    assert decision.matched_company == "Oracle"


def test_newsletter_with_list_unsubscribe_header_is_blocked():
    """Marketing newsletter with List-Unsubscribe header is blocked when no placement signal exists."""
    decision = evaluate_personal_email(
        sender="newsletter@medium.com",
        subject="Top 10 Python Tips You Must Know",
        labels=["INBOX"],
        headers={"List-Unsubscribe": "<mailto:unsub@medium.com>"},
        known_companies=["Google"],
        body_snippet="Here are 10 neat tricks in Python 3.12...",
    )

    assert decision.is_allowed is False
    assert "List-Unsubscribe" in decision.reason


def test_direct_personal_email_is_allowed():
    """Legitimate 1-on-1 personal email with no spam flags is allowed."""
    decision = evaluate_personal_email(
        sender="professor.doe@college.edu",
        subject="Project consultation meeting",
        labels=["INBOX"],
        headers={},
        known_companies=["Google"],
        body_snippet="Hi Pranav, let's meet tomorrow at 11 AM to discuss your project.",
    )

    assert decision.is_allowed is True
    assert "Direct personal email" in decision.reason
