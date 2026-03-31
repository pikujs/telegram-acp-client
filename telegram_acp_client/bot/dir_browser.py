import logging
import os
import asyncio

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.callback_router import router
from telegram_acp_client.bot.messaging import (
    safe_reply,
    safe_answer,
    safe_edit,
    send_safe_message,
    typing_action,
)
from telegram_acp_client.services.acp_service import acp_service
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.bot.agent import start_agent_service
from telegram_acp_client.bot.threads import (
    extract_thread_id,
    get_current_session_id,
    set_current_session_id,
)
from telegram_acp_client.bot.formatting import escape_markdown


logger = logging.getLogger(__name__)


def build_directory_browser_keyboard(current_path: str, session_name: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Builds an interactive inline keyboard for navigating directories."""
    current_path = os.path.abspath(current_path)
    
    try:
        items = os.listdir(current_path)
    except Exception as e:
        logger.error(f"Error listing directory {current_path}: {e}")
        items = []

    # Filter for directories and sort
    directories = []
    for item in items:
        full_path = os.path.join(current_path, item)
        if os.path.isdir(full_path):
            directories.append(item)
            
    directories.sort()

    items_per_page = 5
    total_pages = max(1, (len(directories) + items_per_page - 1) // items_per_page)
    
    if page < 0: page = 0
    if page >= total_pages: page = total_pages - 1
    
    keyboard = []
    
    # ".." Parent Directory button
    parent_dir = os.path.dirname(current_path)
    if parent_dir and parent_dir != current_path:
        keyboard.append([InlineKeyboardButton("⬆️ .. (Parent Directory)", callback_data=("dir_nav", session_name, parent_dir, 0))])
        
    if total_pages > 0:
        start_idx = page * items_per_page
        end_idx = start_idx + items_per_page
        page_dirs = directories[start_idx:end_idx]

        # Directories
        for d in page_dirs:
            d_path = os.path.join(current_path, d)
            keyboard.append([InlineKeyboardButton(f"📁 {d}", callback_data=("dir_nav", session_name, d_path, 0))])
            
        # Pagination
        if page < total_pages - 1:
            keyboard.append([InlineKeyboardButton("More", callback_data=("dir_nav", session_name, current_path, page + 1))])

    # Select Current
    keyboard.append([InlineKeyboardButton("✅ Select Current Directory", callback_data=("dir_sel", session_name, current_path))])
    
    text = f"""📂 **Select Path for `{escape_markdown(session_name)}`**

`{escape_markdown(current_path)}`

_Page {page + 1}/{total_pages if total_pages > 0 else 1}_"""
    
    return text, InlineKeyboardMarkup(keyboard)


@router.register("dir_nav")
async def on_dir_nav(update: Update, context: ContextTypes.DEFAULT_TYPE, session_name: str, path: str, page: int):
    query = update.callback_query
    text, keyboard = build_directory_browser_keyboard(path, session_name, int(page))
    await safe_edit(query, text, reply_markup=keyboard, parse_mode="Markdown")

@router.register("dir_sel")
async def on_dir_sel(update: Update, context: ContextTypes.DEFAULT_TYPE, session_name: str, path: str):
    query = update.callback_query
    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)
    
    if thread_id:
        existing_sid = await get_current_session_id(update, context)
        if existing_sid:
            await safe_answer(query, "This topic already has a session linked.", show_alert=True)
            return

    # Clear pending state if it exists
    pending_name_key = f"pending_new_session_{thread_id}_name"
    pending_path_key = f"pending_new_session_{thread_id}_path"
    context.user_data.pop(pending_name_key, None)
    context.user_data.pop(pending_path_key, None)

    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        await safe_answer(query, f"Invalid path: {e}", show_alert=True)
        return

    await safe_edit(query.message, f"""✅ Selected path: `{path}`
Creating session `{session_name}`...""", parse_mode="Markdown")
    
    async with typing_action(context, chat_id):
        db_id = await db_service.create_session(chat_id, thread_id, session_name, path)
        if thread_id:
            await set_current_session_id(update, context, db_id, thread_id)
        await start_agent_service(update, context, db_id, path, thread_id)
