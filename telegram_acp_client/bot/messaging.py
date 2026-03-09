import asyncio
import logging
import contextlib
import html
from telegram.constants import ChatAction
from telegram import Update
from telegram.error import RetryAfter, TimedOut, NetworkError

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

async def safe_api_call(func, *args, max_retries=5, **kwargs):
    attempt = 0
    backoff = 1.0
    while attempt <= max_retries:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if isinstance(e, RetryAfter):
                await asyncio.sleep(e.retry_after)
                continue
            elif isinstance(e, (TimedOut, NetworkError)):
                attempt += 1
                if attempt > max_retries:
                    raise e
                await asyncio.sleep(backoff)
                backoff *= 2
            else:
                raise e

async def safe_reply(update: Update, text: str, **kwargs):
    if 'parse_mode' not in kwargs:
        kwargs['parse_mode'] = "Markdown"
    try:
        return await safe_api_call(update.message.reply_text, text, **kwargs)
    except Exception as e:
        if "Can't parse entities" in str(e):
            kwargs.pop('parse_mode', None)
            return await safe_api_call(update.message.reply_text, text, **kwargs)
        raise e

async def safe_edit(query, text: str, **kwargs):
    if 'parse_mode' not in kwargs:
        kwargs['parse_mode'] = "Markdown"
    try:
        return await safe_api_call(query.edit_message_text, text, **kwargs)
    except Exception as e:
        if "Can't parse entities" in str(e):
            kwargs.pop('parse_mode', None)
            return await safe_api_call(query.edit_message_text, text, **kwargs)
        raise e

async def safe_answer(query, text: str = None, **kwargs):
    if text is not None:
        return await safe_api_call(query.answer, text=text, **kwargs)
    return await safe_api_call(query.answer, **kwargs)

async def send_safe_message(context, chat_id, text, parse_mode="Markdown", max_retries=5):
    """Tries to send a message with Markdown, falls back to plain text if parsing fails. Includes exponential backoff for network issues."""
    log_text = (text[:100] + "...") if len(text) > 100 else text
    logger.info(f"BOT SENDING MESSAGE to {chat_id}: {log_text}")
    
    attempt = 0
    backoff = 1.0  # Initial backoff in seconds

    while attempt <= max_retries:
        try:
            return await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode
            )
        except Exception as e:
            if "Can't parse entities" in str(e):
                logger.warning(f"Markdown parsing failed, falling back to plain text: {e}")
                return await context.bot.send_message(chat_id=chat_id, text=text)
            
            if isinstance(e, RetryAfter):
                wait_time = e.retry_after
                logger.warning(f"Rate limited by Telegram. Retrying after {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            elif isinstance(e, (TimedOut, NetworkError)):
                attempt += 1
                if attempt > max_retries:
                    logger.error(f"Failed to send message after {max_retries} attempts due to network errors: {e}")
                    raise e
                
                logger.warning(f"Network error when sending message ({e}). Retrying in {backoff}s (Attempt {attempt}/{max_retries})...")
                await asyncio.sleep(backoff)
                backoff *= 2  # Exponential backoff
            else:
                # Re-raise unexpected exceptions
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