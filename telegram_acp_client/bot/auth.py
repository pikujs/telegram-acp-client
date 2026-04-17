import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from telegram_acp_client.config import settings

logger = logging.getLogger(__name__)

def authorized_only(func):
    """Decorator to restrict access to authorized users only."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user:
            return

        user_id = update.effective_user.id
        if user_id not in settings.ALLOWED_USER_IDS:
            username = update.effective_user.username or "Unknown"
            logger.warning(f"Unauthorized access attempt by {username} ({user_id})")
            if update.message:
                from telegram_acp_client.bot.messaging import safe_reply
                await safe_reply(update, "⛔ You are not authorized to use this bot.")
            elif update.callback_query:
                from telegram_acp_client.bot.messaging import safe_answer
                await safe_answer(update.callback_query, "⛔ Unauthorized", show_alert=True)
            return

        return await func(update, context, *args, **kwargs)
    return wrapper

async def is_authorized(update) -> bool:
    """Check if the user is in the whitelist."""
    user_id = update.effective_user.id
    if user_id not in settings.ALLOWED_USER_IDS:
        username = update.effective_user.username or "Unknown"
        logger.warning(f"Unauthorized access attempt by {username} ({user_id})")
        from telegram_acp_client.bot.messaging import safe_reply
        await safe_reply(update, "⛔ You are not authorized to use this bot.")
        return False
    return True

