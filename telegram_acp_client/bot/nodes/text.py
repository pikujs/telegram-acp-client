from typing import Any, Optional
from telegram_acp_client.bot.nodes.base import InteractionNode

class TextNode(InteractionNode):
    """
    Handles text (messages/thoughts) from the agent.
    Uses the session-level DraftStreamer for UI updates.
    """
    def __init__(
        self, 
        context, chat_id, session_id, entity_id, 
        thread_id=None, role="agent", prefix="", 
        streamer=None
    ):
        super().__init__(context, chat_id, session_id, entity_id, thread_id)
        self.role = role
        self.prefix = prefix
        self.text = ""
        self.streamer = streamer # SessionDraftStreamer instance

    def update(self, update: Any) -> bool:
        if not update:
            return False
        
        # Handle syncing from a TextEntity object (for initial sync)
        if hasattr(update, "text") and isinstance(update.text, str):
            if self.text != update.text:
                self.text = update.text
                return True
            return False
            
        # Handle string chunks (from ACPService)
        if isinstance(update, str):
            self.text += update
            return True
            
        return False

    async def render(self):
        if self.streamer:
            await self.streamer.stream_node(self)

    async def finalize(self):
        if self.streamer:
            await self.streamer.finalize_node(self)
