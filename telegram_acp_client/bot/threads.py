from telegram import Update
from telegram.ext import ContextTypes
from telegram_acp_client.services.db_service import db_service


def extract_thread_id(update: Update) -> int | None:
    """Extracts the message_thread_id from the update if available."""
    if update.message and update.message.is_topic_message:
        return update.message.message_thread_id
    if (
        update.callback_query
        and update.callback_query.message
        and getattr(update.callback_query.message, "is_topic_message", False)
    ):
        return update.callback_query.message.message_thread_id
    return None


async def get_current_session_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int | None:
    """Gets the active session ID for the current chat and thread."""
    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)

    # Check cache first
    cache_key = f"current_session_id_{chat_id}_{thread_id or 0}"
    if cache_key in context.user_data:
        return context.user_data[cache_key]

    # Fallback to DB
    sid = await db_service.get_last_session_id(chat_id, thread_id)
    if sid:
        context.user_data[cache_key] = sid
    return sid


async def set_current_session_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session_id: int,
    thread_id: int | None = None,
):
    """Sets the active session ID for the current chat and thread. Persists to DB."""
    chat_id = update.effective_chat.id
    if thread_id is None:
        thread_id = extract_thread_id(update)
    cache_key = f"current_session_id_{chat_id}_{thread_id or 0}"
    context.user_data[cache_key] = session_id
    await db_service.set_last_session(chat_id, thread_id, session_id)


def clear_current_session_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the active session ID for the current chat and thread."""
    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)
    cache_key = f"current_session_id_{chat_id}_{thread_id or 0}"
    if cache_key in context.user_data:
        del context.user_data[cache_key]
