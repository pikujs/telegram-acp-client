from typing import Any
from telegram_acp_client.bot.nodes.base import InteractionNode
from telegram_acp_client.bot.messaging import send_safe_message, safe_edit

class ModeNode(InteractionNode):
    """
    Renders and manages mode switch updates.
    """
    def __init__(self, context, chat_id, session_id, entity_id, thread_id=None):
        super().__init__(context, chat_id, session_id, entity_id, thread_id)
        self.current_mode = "unknown"

    def update(self, update: Any) -> bool:
        mode = getattr(update, "mode", str(update))
        if self.current_mode != mode:
            self.current_mode = mode
            return True
        return False

    async def render(self):
        text = f"⚙️ *Mode Switched:* `{self.current_mode}`"
        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, message_thread_id=self.thread_id)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text)
