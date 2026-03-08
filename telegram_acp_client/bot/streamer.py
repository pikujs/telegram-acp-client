import asyncio
import contextlib
import logging
from telegram.error import BadRequest

from telegram_acp_client.services.db_service import db_service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000

class MessageStreamer:
    def __init__(self, context, chat_id, db_id):
        self.context = context
        self.chat_id = chat_id
        self.db_id = db_id
        self.messages = []
        self.buffer = ""
        self.last_sent_text = ""
        self._updater_task = None
        self._stop_event = asyncio.Event()
        self._init_lock = asyncio.Lock()

    async def start(self):
        """Starts the background task that periodically updates the message."""
        self._updater_task = asyncio.create_task(self._update_loop())

    async def add_text(self, text_chunk: str):
        """Adds new text to the buffer."""
        self.buffer += text_chunk
        
        # If we haven't sent the initial message yet, send it now
        if not self.messages and self.buffer.strip():
            async with self._init_lock:
                if not self.messages:
                    try:
                        chunk = self.buffer[:MAX_MESSAGE_LENGTH]
                        msg = await self.context.bot.send_message(
                            chat_id=self.chat_id, 
                            text=chunk
                        )
                        self.messages.append(msg)
                        self.last_sent_text = chunk
                    except Exception as e:
                        logger.error(f"Error sending initial stream message: {e}")

    async def _update_loop(self):
        """Periodically flushes the buffer to Telegram via edit_message_text."""
        while not self._stop_event.is_set():
            await asyncio.sleep(1.5) # Throttle to avoid Telegram rate limits
            await self._flush()
            
    async def _flush(self):
        # Only edit if we have a message and the text has actually changed
        if not self.messages or self.buffer == self.last_sent_text:
            return

        chunks = [self.buffer[i:i+MAX_MESSAGE_LENGTH] for i in range(0, len(self.buffer), MAX_MESSAGE_LENGTH)]
        if not chunks:
            return

        try:
            # Update the last message we currently have, if its corresponding chunk changed
            current_last_idx = len(self.messages) - 1
            if current_last_idx < len(chunks):
                chunk_for_last_msg = chunks[current_last_idx]
                try:
                    await self.context.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.messages[current_last_idx].message_id,
                        text=chunk_for_last_msg
                    )
                except BadRequest as e:
                    # Ignore "Message is not modified" errors
                    if "Message is not modified" not in str(e):
                        logger.warning(f"Error flushing stream: {e}")
                except Exception as e:
                    logger.warning(f"Error flushing stream: {e}")

            # Send any new messages needed for new chunks
            while len(self.messages) < len(chunks):
                new_msg_idx = len(self.messages)
                msg = await self.context.bot.send_message(
                    chat_id=self.chat_id,
                    text=chunks[new_msg_idx]
                )
                self.messages.append(msg)

            self.last_sent_text = self.buffer
        except Exception as e:
            logger.warning(f"Error flushing stream: {e}")

    async def close(self):
        """Stops the updater and flushes any remaining text, then saves to DB."""
        self._stop_event.set()
        if self._updater_task:
            self._updater_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._updater_task
        await self._flush()
        if self.buffer.strip():
            await db_service.save_message(self.db_id, "agent", self.buffer)
