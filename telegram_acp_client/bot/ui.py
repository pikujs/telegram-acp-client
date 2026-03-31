import logging

from typing import Any, Dict, List, Optional
from telegram import Update
from telegram.ext import ContextTypes

from telegram_acp_client.bot.callback_router import router
from telegram_acp_client.bot.messaging import safe_edit, safe_answer
from telegram_acp_client.bot.formatting import escape_markdown
from telegram_acp_client.services.acp_service import acp_service

# Import nodes
from telegram_acp_client.bot.nodes.base import InteractionNode
from telegram_acp_client.bot.nodes.text import TextNode
from telegram_acp_client.bot.nodes.tool import ToolNode
from telegram_acp_client.bot.nodes.plan import PlanNode
from telegram_acp_client.bot.nodes.mode import ModeNode
from telegram_acp_client.bot.nodes.usage import UsageNode

logger = logging.getLogger(__name__)

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

def create_node(
    context: ContextTypes.DEFAULT_TYPE, 
    chat_id: int, 
    session_id: int, 
    entity_id: str, 
    kind: str, 
    thread_id: Optional[int] = None, 
    **kwargs
) -> InteractionNode:
    """Factory to create the appropriate Node for an interaction kind."""
    if kind in ["message", "thought"]:
        role = kwargs.get("role", "agent")
        prefix = kwargs.get("prefix", "")
        streamer = kwargs.get("streamer")
        return TextNode(context, chat_id, session_id, entity_id, thread_id, role, prefix, streamer=streamer)
    
    if kind == "tool":
        return ToolNode(context, chat_id, session_id, entity_id, thread_id)
    
    if kind == "plan":
        return PlanNode(context, chat_id, session_id, entity_id, thread_id)
    
    if kind == "mode":
        return ModeNode(context, chat_id, session_id, entity_id, thread_id)
    
    if kind == "usage":
        return UsageNode(context, chat_id, session_id, entity_id, thread_id)
    
    # Fallback to base node
    node = InteractionNode(context, chat_id, session_id, entity_id, thread_id)
    node.kind = kind # Ensure kind is set for fallback display
    return node

@router.register("more_output")
async def on_more_output_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid, tc_id):
    query = update.callback_query
    session = acp_service.active_processes.get(sid)
    if session:
        # In the new architecture, session.nodes stores the UI-aware objects
        node = session.nodes.get(tc_id)
        if node and isinstance(node, ToolNode):
            node.data["current_page"] = node.data.get("current_page", 1) + 1
            await node.render()
            await safe_answer(query, "Loading more output...")
        else:
            await safe_answer(query, "Tool output not found.", show_alert=True)
    else:
        await safe_answer(query, "Session not active.", show_alert=True)

