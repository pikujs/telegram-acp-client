"""
Session-level DraftStreamer that manages a queue of text interactions.
Ensures only one draft is active per session to prevent Telegram UI collisions.
Includes outbound throttling to ensure UI stability.
"""
import asyncio
import logging
import random
import time
from typing import Optional, Any

from telegram.error import BadRequest
from telegram_acp_client.bot.messaging import send_message_draft
from telegram_acp_client.bot.formatting import escape_markdown
from telegram_acp_client.services.db_service import db_service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
THROTTLE_INTERVAL = 0.8 # 1 update per 0.8s (slightly faster than 1s)

class SessionDraftStreamer:
    """
    Manages all streaming text for a single bot session.
    
    Features:
    - Random Draft ID: Uses a random long integer for each interaction slot.
    - Sequential Streaming: Ensures thoughts and messages stream in order.
    - Outbound Throttling: Coalesces updates to prevent UI flicker and API spam.
    - Auto-Finalization: Converts drafts to permanent messages after inactivity.
    """
    def __init__(self, context, chat_id, db_id, thread_id=None):
        self.context = context
        self.chat_id = chat_id
        self.db_id = db_id
        self.thread_id = thread_id
        
        # Per docs: Use a large random integer for the draft_id
        self.draft_id = random.randint(1, 2**63 - 1)
        
        self.active_node = None
        self.lock = asyncio.Lock()
        self._auto_finalize_task = None
        self._throttle_task = None
        self._last_update_time = 0
        self._last_draft_text = ""

    async def stream_node(self, node):
        """Updates the current draft with content from the given node."""
        async with self.lock:
            # If switching nodes, finalize the previous one first
            if self.active_node and self.active_node != node:
                logger.debug(f"Switching streamer from {self.active_node.entity_id} to {node.entity_id}")
                await self._finalize_active()
                # New node gets a new random draft_id
                self.draft_id = random.randint(1, 2**63 - 1)
            
            self.active_node = node
            
            # Schedule a throttled UI update
            await self._schedule_update()
            await self._reset_auto_finalize_timer()

    async def _schedule_update(self):
        """Ensures updates are sent no faster than THROTTLE_INTERVAL."""
        now = time.time()
        elapsed = now - self._last_update_time
        
        if elapsed >= THROTTLE_INTERVAL:
            # Can update immediately
            if self._throttle_task:
                self._throttle_task.cancel()
                self._throttle_task = None
            await self._update_ui()
        else:
            # Schedule for later if not already scheduled
            if not self._throttle_task:
                delay = THROTTLE_INTERVAL - elapsed
                self._throttle_task = asyncio.create_task(self._delayed_update(delay))

    async def _delayed_update(self, delay: float):
        try:
            await asyncio.sleep(delay)
            async with self.lock:
                await self._update_ui()
                self._throttle_task = None
        except asyncio.CancelledError:
            pass

    async def _update_ui(self):
        """Sends the current buffer of the active node as a draft."""
        if not self.active_node:
            return
            
        buffer = self.active_node.prefix + self.active_node.text
        if not buffer.strip():
            return

        self._last_update_time = time.time()

        try:
            num_complete_chunks = len(buffer) // MAX_MESSAGE_LENGTH
            remaining_chars = len(buffer) % MAX_MESSAGE_LENGTH
            
            # 1. Handle complete chunks (Finalize them as permanent messages)
            for i in range(num_complete_chunks):
                if i >= len(self.active_node.messages):
                    start = i * MAX_MESSAGE_LENGTH
                    end = start + MAX_MESSAGE_LENGTH
                    chunk_text = buffer[start:end]
                    
                    msg = await self.context.bot.send_message(
                        chat_id=self.chat_id,
                        text=escape_markdown(chunk_text),
                        parse_mode="Markdown",
                        message_thread_id=self.thread_id,
                    )
                    if msg: 
                        self.active_node.messages.append(msg)
                        self._last_draft_text = "" # Reset draft tracking for new chunk

            # 2. Handle active chunk (The actual "Draft" streaming part)
            if remaining_chars > 0:
                last_chunk = buffer[num_complete_chunks * MAX_MESSAGE_LENGTH:]
                safe_text = escape_markdown(last_chunk)
                
                if len(self.active_node.messages) == num_complete_chunks:
                    # Only send if text actually changed
                    if safe_text != self._last_draft_text:
                        await send_message_draft(
                            self.context,
                            self.chat_id,
                            safe_text,
                            draft_id=self.draft_id,
                            parse_mode="Markdown",
                            message_thread_id=self.thread_id,
                        )
                        self._last_draft_text = safe_text
        except Exception as e:
            logger.warning(f"Error in SessionStreamer update: {e}")

    async def finalize_node(self, node):
        """Finalizes a specific node, converting its draft to a permanent message."""
        async with self.lock:
            if self.active_node == node:
                await self._finalize_active()

    async def _finalize_active(self):
        """Converts active draft to permanent message and clears active_node."""
        if not self.active_node:
            return
            
        # Cancel any pending throttled update
        if self._throttle_task:
            self._throttle_task.cancel()
            self._throttle_task = None

        buffer = self.active_node.prefix + self.active_node.text
        num_complete_chunks = len(buffer) // MAX_MESSAGE_LENGTH
        remaining_chars = len(buffer) % MAX_MESSAGE_LENGTH
        
        try:
            # 1. Send the final (possibly incomplete) chunk as a REAL permanent message FIRST
            if remaining_chars > 0 and len(self.active_node.messages) == num_complete_chunks:
                last_chunk = buffer[num_complete_chunks * MAX_MESSAGE_LENGTH:]
                logger.info(f"Finalizing node {self.active_node.entity_id}: sending permanent message")
                msg = await self.context.bot.send_message(
                    chat_id=self.chat_id,
                    text=escape_markdown(last_chunk),
                    parse_mode="Markdown",
                    message_thread_id=self.thread_id,
                )
                if msg: self.active_node.messages.append(msg)
            
            # 2. Clear the draft area AFTER the permanent message is sent to reset the UI
            try:
                await send_message_draft(
                    self.context, self.chat_id, "", self.draft_id, message_thread_id=self.thread_id
                )
            except Exception: pass # Best effort cleanup
            
            # 3. Persistence: Save the final clean text to the database
            content_to_save = self.active_node.text
            if content_to_save.strip():
                await db_service.save_message(self.db_id, self.active_node.role, content_to_save)
                
        except Exception as e:
            logger.warning(f"Error finalizing node {self.active_node.entity_id}: {e}")
        finally:
            self.active_node = None
            self._last_draft_text = ""
            self._last_update_time = 0

    async def _reset_auto_finalize_timer(self):
        if self._auto_finalize_task:
            self._auto_finalize_task.cancel()
        self._auto_finalize_task = asyncio.create_task(self._auto_finalize_after_delay(4.0))

    async def _auto_finalize_after_delay(self, delay: float):
        try:
            await asyncio.sleep(delay)
            async with self.lock:
                if self.active_node:
                    logger.debug(f"Auto-finalizing idle stream for chat {self.chat_id}")
                    await self._finalize_active()
        except asyncio.CancelledError:
            pass

    async def close(self):
        """Ensures everything is finalized when the session ends."""
        if self._auto_finalize_task:
            self._auto_finalize_task.cancel()
        if self._throttle_task:
            self._throttle_task.cancel()
        async with self.lock:
            await self._finalize_active()
