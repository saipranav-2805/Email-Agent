import logging
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from app.bot.handlers import (
    backfill_command_handler,
    callback_query_handler,
    help_command_handler,
    pipeline_command_handler,
    start_command_handler,
    text_message_handler,
)
from app.config import settings

logger = logging.getLogger(__name__)

telegram_app: Optional[Application] = None


def get_telegram_application() -> Optional[Application]:
    """Builds and caches the python-telegram-bot Application."""
    global telegram_app
    if telegram_app is not None:
        return telegram_app

    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set. Telegram bot application disabled.")
        return None

    try:
        app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

        # Register handlers
        app.add_handler(CommandHandler("start", start_command_handler))
        app.add_handler(CommandHandler("help", help_command_handler))
        app.add_handler(CommandHandler("pipeline", pipeline_command_handler))
        app.add_handler(CommandHandler("backfill", backfill_command_handler))
        app.add_handler(CallbackQueryHandler(callback_query_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

        telegram_app = app
        return telegram_app
    except Exception as e:
        logger.error(f"Failed to build Telegram Application: {e}")
        return None


async def init_telegram_webhook() -> bool:
    """Configures Telegram webhook on FastAPI startup."""
    app = get_telegram_application()
    if not app:
        return False

    if not settings.APP_BASE_URL or "localhost" in settings.APP_BASE_URL:
        logger.info("Local environment detected. Skipping Telegram webhook registration (polling mode or ngrok needed).")
        await app.initialize()
        return True

    webhook_url = f"{settings.APP_BASE_URL.rstrip('/')}/telegram/webhook"
    secret_token = settings.TELEGRAM_WEBHOOK_SECRET

    try:
        await app.initialize()
        await app.bot.set_webhook(
            url=webhook_url,
            secret_token=secret_token,
            drop_pending_updates=True,
        )
        logger.info(f"Registered Telegram Webhook: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Failed to set Telegram webhook: {e}")
        return False


async def process_telegram_update(update_dict: dict):
    """Processes incoming Telegram update payload from FastAPI route."""
    app = get_telegram_application()
    if not app:
        logger.warning("Received Telegram update but Telegram app is not initialized.")
        return

    update = Update.de_json(update_dict, app.bot)
    await app.process_update(update)


async def get_active_chat_id() -> Optional[str]:
    """Retrieves the active user chat ID from memory, settings, or database."""
    bot_id_str = (settings.TELEGRAM_BOT_TOKEN or "").split(":")[0]

    # 1. Check in-memory setting first (must not be the bot's own ID)
    if settings.TELEGRAM_CHAT_ID and str(settings.TELEGRAM_CHAT_ID) != bot_id_str:
        return str(settings.TELEGRAM_CHAT_ID)

    # 2. Check database user_preferences for captured telegram_chat_id
    try:
        from sqlalchemy import select
        from app.db.models import UserPreference
        from app.db.session import get_db_session

        async with get_db_session() as session:
            stmt = select(UserPreference).where(
                UserPreference.category == "system_config",
                UserPreference.rule_text.startswith("telegram_chat_id:"),
            ).order_by(UserPreference.id.desc())
            pref = (await session.execute(stmt)).scalars().first()
            if pref:
                cid = pref.rule_text.split(":", 1)[1].strip()
                if cid and cid != bot_id_str:
                    settings.TELEGRAM_CHAT_ID = cid
                    return cid
    except Exception as e:
        logger.debug(f"Could not retrieve chat_id from DB: {e}")

    return None


async def send_telegram_notification(
    text: str,
    reply_markup=None,
    parse_mode: str = "MarkdownV2",
) -> Optional[int]:
    """Dispatches a notification message to the configured TELEGRAM_CHAT_ID."""
    app = get_telegram_application()
    target_chat_id = await get_active_chat_id()

    if not app or not target_chat_id:
        logger.info(f"[TELEGRAM SIMULATION] Message to {target_chat_id or 'UNCONFIGURED'}:\n{text}")
        return None

    try:
        sent = await app.bot.send_message(
            chat_id=target_chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_web_page_preview=False,
        )
        return sent.message_id
    except Exception as e:
        logger.warning(f"Failed to send MarkdownV2 Telegram notification: {e}. Retrying with plain text...")
        try:
            # Fallback to plain text if MarkdownV2 parsing fails
            clean_text = text.replace("\\", "").replace("*", "").replace("_", "").replace("`", "")
            sent = await app.bot.send_message(
                chat_id=target_chat_id,
                text=clean_text,
                reply_markup=reply_markup,
            )
            return sent.message_id
        except Exception as retry_e:
            logger.error(f"Failed to send plain text fallback notification: {retry_e}")
            return None
