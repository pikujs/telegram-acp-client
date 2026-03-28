from typing import Any
from telegram_acp_client.bot.nodes.base import InteractionNode
from telegram_acp_client.bot.messaging import send_safe_message, safe_edit

class UsageNode(InteractionNode):
    """
    Tracks and renders token usage statistics.
    """
    def __init__(self, context, chat_id, session_id, entity_id, thread_id=None):
        super().__init__(context, chat_id, session_id, entity_id, thread_id)
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def update(self, update: Any) -> bool:
        usage = getattr(update, "usage", update if isinstance(update, dict) else {})
        self.total_tokens = usage.get("total_tokens", self.total_tokens)
        self.prompt_tokens = usage.get("prompt_tokens", self.prompt_tokens)
        self.completion_tokens = usage.get("completion_tokens", self.completion_tokens)
        return True

    async def render(self):
        text = f"📊 *Token Usage:* {self.total_tokens} (Prompt: {self.prompt_tokens}, Completion: {self.completion_tokens})"
        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, message_thread_id=self.thread_id)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text)
