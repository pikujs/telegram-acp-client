"""
Draft-based MessageStreamer using Telegram Bot API 9.3+ sendMessageDraft.

This provides real-time streaming with typing animation and no "edited" tag.
"""
import asyncio
import logging

from telegram.error import BadRequest

from telegram_acp_client.bot.messaging import send_message_draft, get_next_draft_id
from telegram_acp_client.bot.formatting import escape_markdown
from telegram_acp_client.services.db_service import db_service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000


class DraftMessageStreamer:
    """
    Streams text content using sendMessageDraft API (Bot API 9.3+).
    
    Benefits:
    - Real-time streaming with typing animation
    - No "edited" tag on final message
    - No polling loop needed - immediate updates
    - Natural message breaks when tools/permissions start
    """
    
    def __init__(
        self,
        context,
        chat_id,
        db_id,
        prefix="",
        role="agent",
        parse_mode="Markdown",
        thread_id=None,
    ):
        self.context = context
        self.chat_id = chat_id
        self.db_id = db_id
        self.prefix = prefix
        self.role = role
        self.parse_mode = parse_mode
        self.thread_id = thread_id
        self.messages = []  # List of PERMANENT sent Message objects
        self.buffer = prefix
        self.last_sent_text = ""
        self.last_draft_text = "" # Track last sent draft content to avoid redundant updates
        self.draft_id = None
        self._finalized = False
        self._init_lock = asyncio.Lock()
        self._auto_finalize_task = None

    async def start(self):
        """Initializes the streamer with a fresh draft_id."""
        self.draft_id = get_next_draft_id(self.chat_id, self.thread_id)
        logger.debug(f"DraftMessageStreamer started for chat {self.chat_id}, draft_id={self.draft_id}")
        await self._reset_auto_finalize_timer()

    async def add_text(self, text_chunk: str):
        """Adds new text to the buffer and sends immediately via draft."""
        self.buffer += text_chunk
        
        if not self.buffer.strip():
            return
            
        async with self._init_lock:
            await self._send_draft()
            await self._reset_auto_finalize_timer()

    async def update_buffer(self, full_text: str):
        """Replaces the entire buffer with new text and sends immediately."""
        self.buffer = full_text
        if self.buffer.strip():
            # First update - ensure we have a draft_id
            if self.draft_id is None:
                self.draft_id = get_next_draft_id(self.chat_id, self.thread_id)
            
            async with self._init_lock:
                await self._send_draft()
                await self._reset_auto_finalize_timer()

    async def _reset_auto_finalize_timer(self):
        """Resets the 4-second timer for auto-finalization."""
        if self._auto_finalize_task:
            self._auto_finalize_task.cancel()
        
        self._auto_finalize_task = asyncio.create_task(self._auto_finalize_after_delay(4.0))

    async def _auto_finalize_after_delay(self, delay: float):
        """Closes the stream after a delay of inactivity."""
        try:
            await asyncio.sleep(delay)
            if not self._finalized:
                logger.debug(f"Auto-finalizing stream for chat {self.chat_id} due to {delay}s inactivity")
                await self.close()
        except asyncio.CancelledError:
            pass

    async def _send_draft(self):
        """Sends the current buffer as a draft message using character offset tracking."""
        if not self.buffer.strip():
            return

        try:
            # Calculate how many complete chunks we have
            num_complete_chunks = len(self.buffer) // MAX_MESSAGE_LENGTH
            remaining_chars = len(self.buffer) % MAX_MESSAGE_LENGTH
            
            # 1. Finalize complete chunks (these become permanent messages)
            for i in range(num_complete_chunks):
                if i >= len(self.messages):
                    # This chunk has never been sent as a permanent message
                    start = i * MAX_MESSAGE_LENGTH
                    end = start + MAX_MESSAGE_LENGTH
                    chunk_text = self.buffer[start:end]
                    
                    logger.debug(f"Finalizing complete chunk {i}: {len(chunk_text)} chars")
                    msg = await self.context.bot.send_message(
                        chat_id=self.chat_id,
                        text=escape_markdown(chunk_text),
                        parse_mode=self.parse_mode,
                        message_thread_id=self.thread_id,
                    )
                    if msg:
                        self.messages.append(msg)
                        self.last_draft_text = "" # Reset draft tracking for new chunk

            # 2. Handle the active (possibly incomplete) chunk
            if remaining_chars > 0:
                last_chunk_start = num_complete_chunks * MAX_MESSAGE_LENGTH
                last_chunk = self.buffer[last_chunk_start:]
                safe_last_chunk = escape_markdown(last_chunk)
                
                # Check if we already have a permanent message for this part (e.g. from timer close)
                if len(self.messages) == num_complete_chunks + 1:
                    if self._finalized:
                        # Ensure the existing permanent message matches the current buffer
                        if getattr(self.messages[-1], "text", "") != safe_last_chunk:
                            await self._edit_message(self.messages[-1], last_chunk)
                    return # No drafting needed if permanent message exists

                # No permanent message yet, use draft or send final
                if len(self.messages) == num_complete_chunks:
                    if self._finalized:
                        # Agent is done. 
                        # 1. Clear the active draft area by sending an empty draft
                        try:
                            await send_message_draft(
                                self.context,
                                self.chat_id,
                                "",
                                draft_id=self.draft_id,
                                message_thread_id=self.thread_id,
                            )
                        except Exception: pass # Best effort cleanup

                        # 2. Send final part as PERMANENT message
                        logger.info(f"Finalizing stream for {self.chat_id}: Sending permanent message ({len(last_chunk)} chars)")
                        msg = await self.context.bot.send_message(
                            chat_id=self.chat_id,
                            text=safe_last_chunk,
                            parse_mode=self.parse_mode,
                            message_thread_id=self.thread_id,
                        )
                        if msg: 
                            self.messages.append(msg)
                            logger.info(f"Permanent message sent: {msg.message_id}")
                    else:
                        # Agent is still streaming, send as DRAFT
                        if safe_last_chunk != self.last_draft_text:
                            await send_message_draft(
                                self.context,
                                self.chat_id,
                                safe_last_chunk,
                                draft_id=self.draft_id,
                                parse_mode=self.parse_mode,
                                message_thread_id=self.thread_id,
                            )
                            self.last_draft_text = safe_last_chunk
            
            self.last_sent_text = self.buffer

        except BadRequest as e:
            if "Can't parse entities" in str(e):
                await self._send_draft_fallback()
            elif "Message is not modified" not in str(e):
                logger.warning(f"Error in _send_draft: {e}")
        except Exception as e:
            logger.warning(f"General error in _send_draft: {e}")

    async def _send_draft_fallback(self):
        """Fallback method when parse_mode fails."""
        num_complete_chunks = len(self.buffer) // MAX_MESSAGE_LENGTH
        remaining_chars = len(self.buffer) % MAX_MESSAGE_LENGTH

        for i in range(num_complete_chunks):
            if i >= len(self.messages):
                start = i * MAX_MESSAGE_LENGTH
                end = start + MAX_MESSAGE_LENGTH
                msg = await self.context.bot.send_message(
                    chat_id=self.chat_id,
                    text=self.buffer[start:end],
                    message_thread_id=self.thread_id,
                )
                if msg: self.messages.append(msg)
        
        if remaining_chars > 0 and len(self.messages) == num_complete_chunks:
            last_chunk = self.buffer[num_complete_chunks * MAX_MESSAGE_LENGTH:]
            msg = await self.context.bot.send_message(
                chat_id=self.chat_id,
                text=last_chunk,
                message_thread_id=self.thread_id,
            )
            if msg: self.messages.append(msg)
        
        self.last_sent_text = self.buffer

    async def _edit_message(self, msg, text: str, parse_mode=None):
        """Helper to edit a message with fallback handling."""
        target_parse_mode = parse_mode if parse_mode is not None else self.parse_mode
        safe_text = escape_markdown(text) if target_parse_mode == "Markdown" else text
        
        try:
            await self.context.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=msg.message_id,
                text=safe_text,
                parse_mode=target_parse_mode,
            )
            msg.text = text
        except BadRequest as e:
            if "Can't parse entities" in str(e) and target_parse_mode is not None:
                await self.context.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=msg.message_id,
                    text=text,
                )
                msg.text = text
            elif "Message is not modified" not in str(e):
                logger.warning(f"Edit failed: {e}")

    async def close(self):
        """
        Finalizes the stream by ensuring all content is sent as permanent messages.
        Then saves content to database.
        """
        if self._auto_finalize_task:
            self._auto_finalize_task.cancel()
            self._auto_finalize_task = None

        if self._finalized:
            return

        self._finalized = True
        
        async with self._init_lock:
            await self._send_draft()
        
        content_to_save = (
            self.buffer[len(self.prefix):]
            if self.buffer.startswith(self.prefix)
            else self.buffer
        )
        if content_to_save.strip():
            await db_service.save_message(self.db_id, self.role, content_to_save)
        
        logger.debug(f"DraftMessageStreamer closed for chat {self.chat_id}, saved {len(content_to_save)} chars to DB")
