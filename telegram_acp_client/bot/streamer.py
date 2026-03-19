import asyncio
import logging

from telegram.error import BadRequest

from telegram_acp_client.services.db_service import db_service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000

class MessageStreamer:
    def __init__(self, context, chat_id, db_id, prefix="", role="agent", parse_mode="Markdown"):
        self.context = context
        self.chat_id = chat_id
        self.db_id = db_id
        self.prefix = prefix
        self.role = role
        self.parse_mode = parse_mode
        self.messages = []
        self.buffer = prefix
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
                            text=chunk,
                            parse_mode=self.parse_mode
                        )
                        self.messages.append(msg)
                        self.last_sent_text = chunk
                    except Exception as e:
                        if "Can't parse entities" in str(e):
                            try:
                                msg = await self.context.bot.send_message(
                                    chat_id=self.chat_id,
                                    text=chunk
                                )
                                self.messages.append(msg)
                                self.last_sent_text = chunk
                            except Exception as inner_e:
                                logger.error(f"Error sending initial stream message fallback: {inner_e}")
                        else:
                            logger.error(f"Error sending initial stream message: {e}")

    async def _update_loop(self):
        """Periodically flushes the buffer to Telegram via edit_message_text."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(0.8) # Slightly faster update cycle
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in streamer update loop: {e}")

    async def _flush(self):
        # Only edit if we have a message and the text has actually changed
        if not self.messages or self.buffer == self.last_sent_text:
            return

        # Split buffer into chunks
        chunks = []
        for i in range(0, len(self.buffer), MAX_MESSAGE_LENGTH):
            chunks.append(self.buffer[i:i+MAX_MESSAGE_LENGTH])

        if not chunks:
            return

        try:
            # 1. Update existing messages if they changed
            for i, msg in enumerate(self.messages):
                if i < len(chunks) and (i == len(self.messages) - 1 or chunks[i] != msg.text):
                    # We only really need to update the last message or if we somehow missed a chunk
                    if chunks[i] != self.last_sent_text[i*MAX_MESSAGE_LENGTH:(i+1)*MAX_MESSAGE_LENGTH] if self.last_sent_text else True:
                        try:
                            await self.context.bot.edit_message_text(
                                chat_id=self.chat_id,
                                message_id=msg.message_id,
                                text=chunks[i],
                                parse_mode=self.parse_mode
                            )
                        except BadRequest as e:
                            if "Can't parse entities" in str(e):
                                # Fallback to no parse mode for this chunk
                                try:
                                    await self.context.bot.edit_message_text(
                                        chat_id=self.chat_id,
                                        message_id=msg.message_id,
                                        text=chunks[i]
                                    )
                                except Exception as inner_e:
                                    logger.warning(f"Fallback edit failed: {inner_e}")
                            elif "Message is not modified" not in str(e):
                                logger.warning(f"Error editing message {i}: {e}")
                        except Exception as e:
                            logger.warning(f"Unexpected error editing message {i}: {e}")

            # 2. Append new messages for new chunks
            success = True
            while len(self.messages) < len(chunks):
                idx = len(self.messages)
                try:
                    new_msg = await self.context.bot.send_message(
                        chat_id=self.chat_id,
                        text=chunks[idx],
                        parse_mode=self.parse_mode
                    )
                    self.messages.append(new_msg)
                except Exception as e:
                    if "Can't parse entities" in str(e):
                        try:
                            new_msg = await self.context.bot.send_message(
                                chat_id=self.chat_id,
                                text=chunks[idx]
                            )
                            self.messages.append(new_msg)
                        except Exception as inner_e:
                            logger.error(f"Error sending new stream chunk {idx} fallback: {inner_e}")
                            success = False
                            break
                    else:
                        logger.error(f"Error sending new stream chunk {idx}: {e}")
                        success = False
                        break # Stop trying to send new chunks if one fails

            if success:
                self.last_sent_text = self.buffer
            else:
                # Keep last_sent_text synced with the chunks we actually have sent messages for
                # so that next time _flush runs, it detects a difference and retries.
                self.last_sent_text = "".join(chunks[:len(self.messages)])
        except Exception as e:
            logger.warning(f"General error in streamer flush: {e}")

    async def close(self):
        """Stops the updater and flushes any remaining text, then saves to DB."""
        self._stop_event.set()
        if self._updater_task:
            # Give it a tiny bit of time to finish its last loop naturally
            # or cancel it if it's taking too long
            try:
                await asyncio.wait_for(self._updater_task, timeout=0.5)
            except (TimeoutError, asyncio.CancelledError):
                if self._updater_task and not self._updater_task.done():
                    self._updater_task.cancel()

        # Final flush to ensure everything is sent
        await self._flush()

        # Remove prefix before saving to DB
        content_to_save = self.buffer[len(self.prefix):] if self.buffer.startswith(self.prefix) else self.buffer
        if content_to_save.strip():
            await db_service.save_message(self.db_id, self.role, content_to_save)
