import logging
from typing import Any, List, Optional
from telegram import Message
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

class InteractionNode:
    """
    Base class for a UI-aware interaction unit.
    A Node unifies data state and Telegram message management.
    """
    def __init__(
        self, 
        context: ContextTypes.DEFAULT_TYPE, 
        chat_id: int, 
        session_id: int, 
        entity_id: str, 
        thread_id: Optional[int] = None
    ):
        self.context = context
        self.chat_id = chat_id
        self.session_id = session_id
        self.entity_id = entity_id
        self.thread_id = thread_id
        
        self.messages: List[Message] = []
        self.status = "pending"
        self.data: dict = {}

    def update(self, update: Any) -> bool:
        """
        Updates internal state from ACP data.
        Returns True if state actually changed.
        """
        return False

    async def apply(self, update: Any):
        """
        Updates state and triggers a render if changed.
        """
        if self.update(update):
            await self.render()

    async def render(self):
        """Refreshes the Telegram UI for this node. Provides a fallback display."""
        # Use kind if set (from create_node factory), otherwise class name
        kind_name = getattr(self, "kind", self.__class__.__name__.replace("Node", "")).title()
        
        from telegram_acp_client.bot.formatting import escape_markdown
        from telegram_acp_client.bot.messaging import safe_edit, send_safe_message
        
        safe_entity_id = escape_markdown(str(self.entity_id))
        text = f"*{kind_name}:* {safe_entity_id}"

        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, message_thread_id=self.thread_id)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text)

    async def finalize(self):
        """Called when the interaction is finished."""
        pass
