import re
from typing import List, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.services.classifier import ClassificationResult, ListTypeEnum, TierEnum


def escape_md(text: Optional[str]) -> str:
    """Escapes special characters for Telegram MarkdownV2 format."""
    if not text:
        return ""
    # Characters that must be escaped in MarkdownV2: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", str(text))


def determine_status_header(classification: ClassificationResult) -> str:
    """Determines the visual badge/header based on tier and flags."""
    red_flags = classification.red_flags_detected or []
    has_vellore_trap = any("vellore" in f.lower() for f in red_flags)

    if classification.list_type == ListTypeEnum.SHORTLISTED_LIST:
        return "🚨 *SHORTLISTED*"
    if classification.tier == TierEnum.TIER_1_CRITICAL:
        if classification.action_required and ("test" in classification.action_required.lower() or "assessment" in classification.action_required.lower()):
            return "⚡ *ONLINE TEST LINK / ASSESSMENT*"
        return "🔴 *URGENT ACTION REQUIRED (<6h)*"
    if classification.tier == TierEnum.TIER_2_ACTION:
        if has_vellore_trap:
            return "🟡 *ELIGIBLE — VELLORE TRAP WARNING*"
        elif red_flags:
            return f"🟡 *ELIGIBLE — {escape_md(red_flags[0].upper())}*"
        return "🟢 *NEW ELIGIBLE OPENING*"
    if classification.tier == TierEnum.TIER_3_WORTH_A_LOOK:
        return "💡 *WORTH A LOOK*"
    return "ℹ️ *PLACEMENT UPDATE*"


def format_telegram_card(
    classification: ClassificationResult,
    source_inbox: str,
    email_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Builds a sharp, scannable MarkdownV2 card with actionable buttons."""
    header = determine_status_header(classification)

    company = classification.company_name or "Campus Opportunity"
    role = classification.role or "Not Specified"
    ctc = classification.ctc or "As per policy / Not disclosed"
    action = classification.action_required or "Review details"
    deadline = classification.deadline_iso or "Not specified"
    summary = classification.concise_summary or ""

    lines = [
        header,
        f"🏢 *Company:* {escape_md(company)}",
        f"💼 *Role:* {escape_md(role)}",
        f"💰 *CTC:* {escape_md(ctc)}",
        f"🎯 *Action:* {escape_md(action)}",
        f"⏰ *Deadline:* {escape_md(deadline)}",
    ]

    # Red flags section
    if classification.red_flags_detected:
        flags_str = ", ".join(classification.red_flags_detected)
        lines.append(f"⚠️ *Red Flags:* 🚨 {escape_md(flags_str)}")

    # Summary
    if summary:
        lines.append(f"\n📋 *Summary:* {escape_md(summary)}")

    # Human opinion (for Tier 3)
    if classification.human_opinion:
        lines.append(f"\n🧠 *Chief of Staff Take:* _{escape_md(classification.human_opinion)}_")

    lines.append(f"\n📥 _Source: {escape_md(source_inbox)}_")

    message_text = "\n".join(lines)

    # Build Buttons
    buttons = []

    # Row 1: Extracted URL buttons (Google Forms, NeoPAT, Test Links)
    url_row = []
    for link in classification.links[:3]:  # max 3 link buttons
        btn_label = "🔗 Open Link"
        l_lower = link.lower()
        if "forms.gle" in l_lower or "docs.google.com/forms" in l_lower:
            btn_label = "📝 Open Form"
        elif "neopat" in l_lower:
            btn_label = "🎓 Open NeoPAT"
        elif "hackerrank" in l_lower:
            btn_label = "💻 HackerRank"
        elif "mettl" in l_lower:
            btn_label = "💻 Mettl Test"
        elif "superset" in l_lower:
            btn_label = "⚡ Superset"

        url_row.append(InlineKeyboardButton(text=btn_label, url=link))

    if url_row:
        buttons.append(url_row)

    # Row 2: Interactive Callback Buttons
    action_row = []
    comp_ref = str(company_id) if company_id else company[:20]
    e_ref = str(email_id) if email_id else "0"

    action_row.append(
        InlineKeyboardButton(text="✅ Mark Applied", callback_data=f"apply:{comp_ref}")
    )
    action_row.append(
        InlineKeyboardButton(text="📅 Add to Calendar", callback_data=f"cal:{e_ref}")
    )
    action_row.append(
        InlineKeyboardButton(text="❌ Ignore", callback_data=f"ignore:{comp_ref}")
    )
    buttons.append(action_row)

    reply_markup = InlineKeyboardMarkup(buttons)
    return message_text, reply_markup
