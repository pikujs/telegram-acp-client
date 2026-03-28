import logging
from typing import Callable, Dict

from telegram.ext import InvalidCallbackData
from telegram_acp_client.bot.messaging import safe_answer

logger = logging.getLogger(__name__)

class CallbackRouter:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, action: str):
        """Decorator to register a function to handle a specific callback action."""
        def decorator(func: Callable):
            self._handlers[action] = func
            return func
        return decorator

    async def handle(self, update, context):
        query = update.callback_query
        data = query.data
        
        if data is InvalidCallbackData:
            await safe_answer(query, "This button is no longer valid.", show_alert=True)
            return

        # Acknowledge the callback immediately (unless the handler wants to do it with an alert, 
        # but PTB says it's good practice. We can also let the handler do it, but previous code did it here.)
        # The previous code did await safe_answer(query,) but it caused issues if we wanted to show_alert=True later?
        # Actually previous code did:
        # await safe_answer(query,)
        await safe_answer(query)
        
        if isinstance(data, tuple) and data:
            action = data[0]
            handler = self._handlers.get(action)
            
            if handler:
                # Call the registered function and pass the rest of the tuple as arguments
                await handler(update, context, *data[1:])
            else:
                logger.warning(f"No callback handler registered for action: {action}")

# Create a global instance to use throughout the app
router = CallbackRouter()
