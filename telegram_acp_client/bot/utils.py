import asyncio
import re
import logging
import difflib
import contextlib
import html
from telegram.constants import ChatAction
from telegram_acp_client.config import settings

logger = logging.getLogger(__name__)

@contextlib.asynccontextmanager
async def typing_action(context, chat_id):
    """Sends typing action periodically while the task is running."""
    stop_event = asyncio.Event()

    async def keep_typing():
        while not stop_event.is_set():
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.wait_for(stop_event.wait(), timeout=4)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        stop_event.set()
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

def format_diff(old_text: str, new_text: str, path: str) -> str:
    """Generates a unified diff string."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    
    diff = difflib.unified_diff(
        old_lines, new_lines, 
        fromfile=f"a/{path}", 
        tofile=f"b/{path}",
        lineterm=""
    )
    result = "\n".join(diff)
    return result if result else "(No changes detected)"

def escape_markdown(text: str) -> str:
    """Helper to escape special characters for Telegram Markdown (v1)."""
    return re.sub(r"([_*`\[])", r"\\\1", text)

def is_approval_option(text: str) -> bool:
    """Checks if a button text or option ID represents an approval."""
    if not text: return False
    keywords = ["allow", "yes", "accept", "approve", "grant", "proceed", "confirm"]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)

from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

def authorized_only(func):
    """Decorator to restrict access to authorized users only."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user or not update.effective_user.username:
            return
        
        username = update.effective_user.username
        if username not in settings.ALLOWED_USERS:
            logger.warning(f"Unauthorized access attempt by @{username}")
            if update.message:
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized", show_alert=True)
            return
            
        return await func(update, context, *args, **kwargs)
    return wrapper

async def is_authorized(update) -> bool:
    """Check if the user is in the whitelist."""
    username = update.effective_user.username
    if username not in settings.ALLOWED_USERS:
        logger.warning(f"Unauthorized access attempt by @{username}")
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return False
    return True

async def send_safe_message(context, chat_id, text, parse_mode="Markdown"):
    """Tries to send a message with Markdown, falls back to plain text if parsing fails."""
    log_text = (text[:100] + "...") if len(text) > 100 else text
    logger.info(f"BOT SENDING MESSAGE to {chat_id}: {log_text}")
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=parse_mode
        )
    except Exception as e:
        if "Can't parse entities" in str(e):
            logger.warning(f"Markdown parsing failed, falling back to plain text: {e}")
            await context.bot.send_message(chat_id=chat_id, text=text)
        else:
            raise e

async def send_split_diff(context, chat_id, diff_text):
    """Splits a large diff into multiple messages, ensuring each is wrapped in HTML code blocks to avoid Markdown parsing errors with nested backticks."""
    if not diff_text:
        return

    MAX_LEN = 3500 # Safe margin under 4096 (lowered slightly to account for HTML escaping bloat)
    lines = diff_text.splitlines()
    current_chunk = []
    current_len = 0

    async def flush_chunk(is_last=False):
        nonlocal current_chunk, current_len
        if not current_chunk: return

        content = "\n".join(current_chunk)
        # Wrap in HTML pre/code tags to avoid Markdown parsing issues
        formatted = f"📝 <b>Proposed Changes:</b>\n<pre><code class=\"language-diff\">{content}</code></pre>"
        if not is_last:
            formatted += "\n<i>(continued in next message...)</i>"

        await send_safe_message(context, chat_id, formatted, parse_mode="HTML")
        current_chunk = []
        current_len = 0

    for line in lines:
        escaped_line = html.escape(line)
        # If adding this line exceeds limit, flush what we have
        if current_len + len(escaped_line) + 1 > MAX_LEN and current_chunk:
            await flush_chunk(is_last=False)

        current_chunk.append(escaped_line)
        current_len += len(escaped_line) + 1

    await flush_chunk(is_last=True)
