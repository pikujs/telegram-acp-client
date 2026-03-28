import logging
from typing import Any, Optional
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram_acp_client.bot.nodes.base import InteractionNode
from telegram_acp_client.bot.formatting import escape_markdown
from telegram_acp_client.bot.messaging import send_safe_message, safe_edit

logger = logging.getLogger(__name__)

class ToolNode(InteractionNode):
    """
    Manages a tool call's state and its evolving Telegram bubble.
    """
    KIND_EMOJIS = {
        "read": "📖", "edit": "📝", "delete": "🗑️", 
        "move": "📦", "search": "🔍", "execute": "⚙️", 
        "think": "💭", "fetch": "🌐", "question": "❓", "other": "🔧"
    }

    def __init__(self, context, chat_id, session_id, entity_id, thread_id=None):
        super().__init__(context, chat_id, session_id, entity_id, thread_id)
        self.title = "unknown"
        self.tool_kind = "other"
        self.raw_input = {}
        self.content = []
        self.data["current_page"] = 1
        self.data["page_size"] = 20

    def update(self, update: Any) -> bool:
        changed = False
        # Sync from legacy ToolEntity if needed
        if hasattr(update, "tool_kind"):
            if self.title != update.title or self.status != update.status or self.tool_kind != update.tool_kind:
                self.title = update.title
                self.status = update.status
                self.tool_kind = update.tool_kind
                self.raw_input = update.raw_input
                self.content = update.content
                return True
            return False

        # Update simple fields from raw update
        for field in ["title", "status"]:
            val = getattr(update, field, None)
            if val is not None and getattr(self, field) != val:
                setattr(self, field, val)
                changed = True

        # Tool kind
        kind = getattr(update, "kind", None)
        if kind is not None:
            if hasattr(kind, "value"):
                kind = kind.value
            if self.tool_kind != kind:
                self.tool_kind = kind
                changed = True

        # Merge raw_input
        ri = getattr(update, "raw_input", None) or (
            update.get("raw_input") if isinstance(update, dict) else None
        )
        if ri and isinstance(ri, dict):
            old_ri = self.raw_input.copy()
            self.raw_input.update(ri)
            if old_ri != self.raw_input:
                changed = True

        # Append content
        content = getattr(update, "content", None)
        if content:
            if isinstance(content, list):
                self.content.extend(content)
            else:
                self.content.append(content)
            changed = True
        return changed

    async def render(self):
        # 1. Format the status line
        status_emoji = self.KIND_EMOJIS.get(self.tool_kind, "🔧")
        status_text = self.status.replace("_", " ").title()
        
        if self.status in ["pending", "in_progress"]:
            progress_prefix = "⏳ "
        elif self.status == "completed":
            progress_prefix = "✅ "
        elif self.status == "failed":
            progress_prefix = "❌ "
        else:
            progress_prefix = ""

        from telegram_acp_client.bot.ui import format_interaction_title
        safe_title = format_interaction_title(
            self.title, 
            self.raw_input, 
            self.tool_kind
        )
        
        if self.tool_kind == "question":
            text = f"❓ *Agent Question:* {safe_title}"
        else:
            text = f"{progress_prefix}{status_emoji} *Tool {status_text}* (`{self.entity_id}`): {safe_title}"

        # 2. Add results if completed
        keyboard = []
        if self.status == "completed" and self.content:
            text += "\n"
            full_buffer = ""
            for item in self.content:
                inner_content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else None)
                if inner_content:
                    extracted = self._robust_extract(inner_content)
                    if extracted: full_buffer += f"\n{extracted}"

            # Paging logic
            all_lines = full_buffer.strip().splitlines()
            limit = 30
            page_size = self.data.get("page_size", 20)
            current_page = self.data.get("current_page", 1)
            visible_count = current_page * page_size

            if len(all_lines) > limit:
                display_lines = all_lines[:visible_count]
                has_more = len(all_lines) > visible_count
                text += "\n" + "\n".join(display_lines)
                text += f"\n\n_(Showing {min(visible_count, len(all_lines))} of {len(all_lines)} lines)_"
                if has_more:
                    keyboard.append([
                        InlineKeyboardButton(
                            "➕ More Output", 
                            callback_data=("more_output", self.session_id, self.entity_id)
                        )
                    ])
            else:
                text += full_buffer

        # 3. Update or send message
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, reply_markup=reply_markup, message_thread_id=self.thread_id)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text, reply_markup=reply_markup)

    def _robust_extract(self, content: Any) -> str | None:
        """Extracts and formats content, ensuring safety for Markdown."""
        if isinstance(content, str): 
            # Wrap in code block for safety and readability
            return f"```\n{content}\n```"
            
        c_type = getattr(content, "type", None) or (content.get("type") if isinstance(content, dict) else None)
        
        if c_type == "text":
            text = getattr(content, "text", None) or (content.get("text") if isinstance(content, dict) else None)
            return f"```\n{text}\n```" if text else None
        elif c_type == "image": 
            return "🖼️ _[Image]_"
        elif c_type == "audio": 
            return "🎵 _[Audio]_"
        elif c_type == "resource":
            res = getattr(content, "resource", None) or (content.get("resource", {}) if isinstance(content, dict) else {})
            uri = getattr(res, "uri", "unknown") or (res.get("uri", "unknown") if isinstance(res, dict) else "unknown")
            return f"📄 _[Resource: {escape_markdown(uri)}]_"
        elif c_type == "resource_link":
            uri = getattr(content, "uri", "unknown") or content.get("uri", "unknown")
            name = getattr(content, "name", "Resource") or content.get("name", "Resource")
            return f"🔗 *[{escape_markdown(name)}]({uri})*"

        if hasattr(content, "text"): 
            return f"```\n{content.text}\n```"
        if isinstance(content, dict) and "text" in content: 
            return f"```\n{content.get('text')}\n```"
        return None
