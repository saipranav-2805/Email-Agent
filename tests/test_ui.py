from app.bot.ui import determine_status_header, escape_md, format_telegram_card
from app.services.classifier import ClassificationResult, ListTypeEnum, TierEnum


def test_telegram_escape_md():
    """Ensures MarkdownV2 special characters are correctly escaped."""
    raw = "Google Inc. (Round 1) - CTC: 25.5 LPA! Check [link] & test_id."
    escaped = escape_md(raw)

    # In MarkdownV2: . ! ( ) [ ] _ - must be escaped
    assert r"\." in escaped
    assert r"\!" in escaped
    assert r"\(" in escaped
    assert r"\)" in escaped
    assert r"\[" in escaped
    assert r"\]" in escaped
    assert r"\_" in escaped
    assert r"\-" in escaped


def test_telegram_card_formatting_tier_1_shortlist():
    """Checks that a shortlist card generates 🚨 SHORTLISTED and appropriate buttons."""
    classification = ClassificationResult(
        tier=TierEnum.TIER_1_CRITICAL,
        company_name="Microsoft",
        role="Software Engineer",
        ctc="45 LPA",
        is_eligible=True,
        list_type=ListTypeEnum.SHORTLISTED_LIST,
        action_required="Join Teams interview call tomorrow at 10 AM",
        deadline_iso="2026-11-25T10:00:00+05:30",
        links=["https://teams.microsoft.com/meeting123"],
        concise_summary="Selected for Round 3 Interview.",
    )

    msg_text, markup = format_telegram_card(
        classification=classification,
        source_inbox="COLLEGE",
        email_id=42,
        company_id=7,
    )

    assert "🚨 *SHORTLISTED*" in msg_text
    assert "Microsoft" in msg_text
    assert "45 LPA" in msg_text

    # Check button layout
    buttons = markup.inline_keyboard
    assert len(buttons) >= 2  # URL row and action row

    # Check action row
    action_buttons = buttons[-1]
    assert any("Mark Applied" in b.text for b in action_buttons)
    assert any("Add to Calendar" in b.text for b in action_buttons)
    assert any("Ignore" in b.text for b in action_buttons)


def test_telegram_card_vellore_trap_header():
    """Checks that Vellore red flag causes 🟡 ELIGIBLE — VELLORE TRAP header."""
    classification = ClassificationResult(
        tier=TierEnum.TIER_2_ACTION,
        company_name="TCS",
        role="Developer",
        is_eligible=True,
        red_flags_detected=["Vellore campus", "service agreement"],
        action_required="Register on NeoPAT",
        links=["https://vit.neopat.in/test"],
    )

    header = determine_status_header(classification)
    assert "VELLORE TRAP" in header
