import logging
import asyncio
from typing import Any, Dict, List, Optional
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from telegram_acp_client.bot.messaging import safe_edit, send_safe_message, safe_api_call
from telegram_acp_client.bot.formatting import escape_markdown
from telegram_acp_client.services.entities import InteractionEntity, TextEntity, ToolEntity, PlanEntity
from telegram_acp_client.bot.streamer import MessageStreamer

logger = logging.getLogger(__name__)

class EntityRenderer:
    """Base class for rendering an InteractionEntity to Telegram message bubbles."""
    def __init__(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, session_id: int, entity: InteractionEntity):
        self.context = context
        self.chat_id = chat_id
        self.session_id = session_id
        self.entity = entity
        self.messages = [] # List of Telegram Message objects

    async def render(self):
        """Refreshes the UI for this entity."""
        pass

    async def finalize(self):
        """Called when the entity is completed/finished."""
        pass

class TextRenderer(EntityRenderer):
    """Renders streaming text (messages or thoughts) using multiple bubbles if needed."""
    def __init__(self, context, chat_id, session_id, entity: TextEntity):
        super().__init__(context, chat_id, session_id, entity)
        self.streamer = MessageStreamer(
            context, chat_id, entity.entity_id,
            prefix=entity.prefix, 
            role=entity.role
        )

    async def render(self):
        if not self.streamer._updater_task:
            await self.streamer.start()
        
        # Use the streamer's own logic to handle buffer updates and initial message sends
        # This ensures we don't bypass the chunking or the edit rate-limiting
        await self.streamer.update_buffer(self.entity.get_display_text())

    async def finalize(self):
        await self.streamer.close()

def format_interaction_title(title: str, ri: dict, kind: str) -> str:
    """Shared helper to format tool/interaction titles with paths and commands."""
    # 1. Start with escaped title
    safe_title = escape_markdown(title)
    
    # 2. Add path if present and not already in title
    path = ri.get("path") or ri.get("file_path") or ri.get("filePath") or ri.get("filepath")
    if path and str(path) not in title:
        safe_title += f": `{escape_markdown(str(path))}`"
        
    # 3. Add command for execute/bash tools
    if kind == "execute" or "bash" in title.lower() or "shell" in title.lower():
        cmd = ri.get("command") or ri.get("cmd") or ri.get("script") or ri.get("code")
        if cmd:
            safe_title += f"\n\n`{escape_markdown(str(cmd))}`"
    
    return safe_title

class ToolRenderer(EntityRenderer):
    """Renders a tool call's progress and results in a single, evolving bubble."""
    
    KIND_EMOJIS = {
        "read": "📖", "edit": "📝", "delete": "🗑️", 
        "move": "📦", "search": "🔍", "execute": "⚙️", 
        "think": "💭", "fetch": "🌐", "question": "❓", "other": "🔧"
    }

    async def render(self):
        # 1. Format the status line
        status_emoji = self.KIND_EMOJIS.get(self.entity.tool_kind, "🔧")
        status_text = self.entity.status.replace("_", " ").title()
        
        # Progress indicator
        if self.entity.status in ["pending", "in_progress"]:
            progress_prefix = "⏳ "
        elif self.entity.status == "completed":
            progress_prefix = "✅ "
        elif self.entity.status == "failed":
            progress_prefix = "❌ "
        else:
            progress_prefix = ""

        # Use shared formatter for the title part
        safe_title = format_interaction_title(
            self.entity.title, 
            self.entity.raw_input, 
            self.entity.tool_kind
        )
        
        # Special formatting for questions
        if self.entity.tool_kind == "question":
            text = f"❓ *Agent Question:* {safe_title}"
        else:
            text = f"{progress_prefix}{status_emoji} *Tool {status_text}* (`{self.entity.entity_id}`): {safe_title}"

        # 2. Add results if completed
        keyboard = []
        if self.entity.status == "completed" and self.entity.content:
            text += "\n"
            full_buffer = ""
            for item in self.entity.content:
                inner_content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else None)
                if inner_content:
                    extracted = self._robust_extract(inner_content)
                    if extracted: full_buffer += f"\n{extracted}"

            # Paging logic for long output
            all_lines = full_buffer.strip().splitlines()
            limit = 30
            page_size = self.entity.data.get("page_size", 20)
            current_page = self.entity.data.get("current_page", 1)
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
                            callback_data=("more_output", self.session_id, self.entity.entity_id)
                        )
                    ])
            else:
                text += full_buffer

        # 3. Update or send message
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text, reply_markup=reply_markup)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text, reply_markup=reply_markup)

    def _robust_extract(self, content: Any) -> str | None:
        """Robustly extract text from any ContentBlock kind."""
        if isinstance(content, str): return content
        
        c_type = getattr(content, "type", None) or (content.get("type") if isinstance(content, dict) else None)
        
        if c_type == "text":
            return getattr(content, "text", None) or (content.get("text") if isinstance(content, dict) else None)
        elif c_type == "image":
            return "🖼️ _[Image]_"
        elif c_type == "audio":
            return "🎵 _[Audio]_"
        elif c_type == "resource":
            res = getattr(content, "resource", None) or (content.get("resource", {}) if isinstance(content, dict) else {})
            uri = getattr(res, "uri", "unknown") or (res.get("uri", "unknown") if isinstance(res, dict) else "unknown")
            return f"📄 _[Resource: {uri}]_"
        
        # Fallback
        if hasattr(content, "text"): return content.text
        if isinstance(content, dict) and "text" in content: return content.get("text")
        return None

class PlanRenderer(EntityRenderer):
    """Renders the high-level agent plan."""
    async def render(self):
        text = self.entity.get_display_text()
        if not self.messages:
            msg = await send_safe_message(self.context, self.chat_id, text)
            if msg: self.messages.append(msg)
        else:
            await safe_edit(self.messages[0], text)

def create_renderer(context, chat_id, session_id, entity: InteractionEntity) -> EntityRenderer:
    """Factory to create the appropriate renderer for an entity kind."""
    if isinstance(entity, TextEntity):
        return TextRenderer(context, chat_id, session_id, entity)
    if isinstance(entity, ToolEntity):
        return ToolRenderer(context, chat_id, session_id, entity)
    if isinstance(entity, PlanEntity):
        return PlanRenderer(context, chat_id, session_id, entity)
    return EntityRenderer(context, chat_id, session_id, entity)
