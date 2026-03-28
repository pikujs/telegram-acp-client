from typing import Any
from telegram_acp_client.bot.nodes.base import InteractionNode
from telegram_acp_client.bot.messaging import send_safe_message, safe_edit
from telegram_acp_client.bot.formatting import escape_markdown

class PlanNode(InteractionNode):
    """
    Renders and manages the high-level agent plan.
    """
    def __init__(self, context, chat_id, session_id, entity_id, thread_id=None):
        super().__init__(context, chat_id, session_id, entity_id, thread_id)
        self.entries = []

    def update(self, update: Any) -> bool:
        self.entries = getattr(update, "entries", [])
        return True

    async def render(self):
        if not self.entries:
            text = "📋 *Plan:* (empty)"
        else:
            # Escape each entry content to prevent Markdown parsing errors
            lines = [f"- [{e.status}] {escape_markdown(e.content)}" for e in self.entries]
            text = "📋 *New Plan:*\n" + "\n".join(lines)

        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, message_thread_id=self.thread_id)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text)
