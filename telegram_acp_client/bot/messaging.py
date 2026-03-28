import asyncio
import contextlib
import html
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)


async def send_message_draft(
    context, chat_id, text, draft_id: int, parse_mode="Markdown", max_retries=3, **kwargs
):
    """
    Send a draft message that displays typing animation without creating a permanent message.
    
    Uses Telegram Bot API 9.3+ sendMessageDraft method for real-time streaming.
    Falls back to regular sendMessage if draft method is unavailable.
    
    Args:
        context: Telegram context
        chat_id: Target chat ID
        text: Draft message text
        draft_id: Monotonically increasing ID for continuous typing animation
        parse_mode: Parse mode (Markdown/HTML)
        max_retries: Maximum retry attempts for network errors
        
    Returns:
        Message object (or None if draft and not yet finalized)
    """
    log_text = (text[:100] + "...") if len(text) > 100 else text
    logger.info(f"DRAFT MESSAGE to {chat_id} (draft_id={draft_id}): {log_text}")
    
    attempt = 0
    backoff = 0.5
    
    while attempt <= max_retries:
        try:
            # Try sendMessageDraft first (Bot API 9.3+)
            if hasattr(context.bot, 'send_message_draft'):
                return await context.bot.send_message_draft(
                    chat_id=chat_id,
                    text=text,
                    draft_id=draft_id,
                    parse_mode=parse_mode,
                    **kwargs
                )
            else:
                # Fallback: Use regular sendMessage for older Bot API versions
                logger.debug("send_message_draft not available, using sendMessage fallback")
                return await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    **kwargs
                )
        except BadRequest as e:
            err_msg = str(e).lower()
            # Handle draft-specific errors
            if "draft" in err_msg or "textdraft" in err_msg:
                logger.warning(f"Draft method failed, falling back to sendMessage: {e}")
                # Fall back to regular send_message
                try:
                    return await context.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except Exception as inner_e:
                    if "Can't parse entities" in str(inner_e):
                        logger.warning(f"Parse failed, retrying without parse_mode: {inner_e}")
                        return await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            **{k: v for k, v in kwargs.items() if k != 'parse_mode'}
                        )
                    raise
            elif "Can't parse entities" in str(e):
                logger.warning(f"Parse failed, retrying without parse_mode: {e}")
                return await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    **{k: v for k, v in kwargs.items() if k != 'parse_mode'}
                )
            elif isinstance(e, RetryAfter):
                wait_time = e.retry_after
                logger.warning(f"Rate limited. Retrying after {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            else:
                raise
        except (TimedOut, NetworkError):
            attempt += 1
            if attempt > max_retries:
                logger.error(f"Failed to send draft after {max_retries} attempts: {e}")
                raise e
            logger.warning(f"Network error (attempt {attempt}/{max_retries}), retrying in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff *= 2
        except Exception as e:
            raise e


@contextlib.asynccontextmanager
async def typing_action(context, chat_id):
    """Sends typing action periodically while the task is running."""
    stop_event = asyncio.Event()

    async def keep_typing():
        while not stop_event.is_set():
            try:
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.TYPING
                )
                await asyncio.wait_for(stop_event.wait(), timeout=4)
            except TimeoutError:
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
    if "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "Markdown"
    try:
        return await safe_api_call(update.message.reply_text, text, **kwargs)
    except Exception as e:
        if "Can't parse entities" in str(e):
            logger.warning(
                f"Markdown parsing failed in safe_reply, falling back to plain text: {e}"
            )
            kwargs.pop("parse_mode", None)
            return await safe_api_call(update.message.reply_text, text, **kwargs)
        logger.error(f"safe_reply failed: {e} | Text length: {len(text)}")
        raise e


async def safe_edit(query, text: str, **kwargs):
    if "parse_mode" not in kwargs:
        kwargs["parse_mode"] = "Markdown"

    # Identify the correct edit method (CallbackQuery uses edit_message_text, Message uses edit_text)
    edit_func = getattr(query, "edit_message_text", getattr(query, "edit_text", None))
    if not edit_func:
        logger.error(f"Object {type(query)} has no edit method")
        return

    try:
        return await safe_api_call(edit_func, text, **kwargs)
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        if "Can't parse entities" in str(e):
            logger.warning(
                f"Markdown parsing failed in safe_edit, falling back to plain text: {e}"
            )
            kwargs.pop("parse_mode", None)
            return await safe_api_call(edit_func, text, **kwargs)
        logger.error(f"safe_edit failed: {e} | Text length: {len(text)}")
        raise e


async def safe_edit_reply_markup(query, reply_markup):
    # Identify the correct method (CallbackQuery uses edit_message_reply_markup, Message uses edit_reply_markup)
    edit_func = getattr(
        query, "edit_message_reply_markup", getattr(query, "edit_reply_markup", None)
    )
    if not edit_func:
        logger.error(f"Object {type(query)} has no edit_reply_markup method")
        return

    try:
        return await safe_api_call(edit_func, reply_markup=reply_markup)
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.error(f"safe_edit_reply_markup failed: {e}")
        raise e


async def safe_answer(query, text: str = None, **kwargs):
    try:
        if text is not None:
            return await safe_api_call(query.answer, text=text, **kwargs)
        return await safe_api_call(query.answer, **kwargs)
    except Exception as e:
        err_msg = str(e)
        if (
            "query is old" in err_msg.lower()
            or "query has already been answered" in err_msg.lower()
        ):
            return
        logger.warning(f"safe_answer failed: {e}")


async def send_safe_message(
    context, chat_id, text, parse_mode="Markdown", max_retries=5, **kwargs
):
    """Tries to send a message with Markdown, falls back to plain text if parsing fails. Includes exponential backoff for network issues."""
    log_text = (text[:100] + "...") if len(text) > 100 else text
    logger.info(f"BOT SENDING MESSAGE to {chat_id}: {log_text}")

    attempt = 0
    backoff = 1.0  # Initial backoff in seconds

    while attempt <= max_retries:
        try:
            return await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs
            )
        except Exception as e:
            if "Can't parse entities" in str(e):
                logger.warning(
                    f"Markdown parsing failed, falling back to plain text: {e}"
                )
                # Remove parse_mode if it failed
                temp_kwargs = kwargs.copy()
                return await context.bot.send_message(
                    chat_id=chat_id, text=text, **temp_kwargs
                )

            if isinstance(e, RetryAfter):
                wait_time = e.retry_after
                logger.warning(
                    f"Rate limited by Telegram. Retrying after {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
                continue
            elif isinstance(e, (TimedOut, NetworkError)):
                attempt += 1
                if attempt > max_retries:
                    logger.error(
                        f"Failed to send message after {max_retries} attempts due to network errors: {e}"
                    )
                    raise e

                logger.warning(
                    f"Network error when sending message ({e}). Retrying in {backoff}s (Attempt {attempt}/{max_retries})..."
                )
                await asyncio.sleep(backoff)
                backoff *= 2  # Exponential backoff
            else:
                # Re-raise unexpected exceptions
                raise e


async def send_split_diff(context, chat_id, diff_text, thread_id=None):
    """Splits a large diff into multiple messages, ensuring each is wrapped in HTML code blocks to avoid Markdown parsing errors with nested backticks."""
    if not diff_text:
        return

    MAX_LEN = 3500  # Safe margin under 4096 (lowered slightly to account for HTML escaping bloat)
    lines = diff_text.splitlines()
    current_chunk = []
    current_len = 0

    async def flush_chunk(is_last=False):
        nonlocal current_chunk, current_len
        if not current_chunk:
            return

        content = "\n".join(current_chunk)
        # Wrap in HTML pre/code tags to avoid Markdown parsing issues
        formatted = f'📝 <b>Proposed Changes:</b>\n<pre><code class="language-diff">{content}</code></pre>'
        if not is_last:
            formatted += "\n<i>(continued in next message...)</i>"

        await send_safe_message(
            context, chat_id, formatted, parse_mode="HTML", message_thread_id=thread_id
        )
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
