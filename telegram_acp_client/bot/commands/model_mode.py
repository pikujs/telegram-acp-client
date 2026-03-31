import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.callback_router import router
from telegram_acp_client.bot.messaging import safe_reply, safe_answer, safe_edit_reply_markup
from telegram_acp_client.services.acp_service import acp_service
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.bot.threads import get_current_session_id
from telegram_acp_client.bot.registry import register_command
from telegram_acp_client.bot.formatting import escape_markdown

logger = logging.getLogger(__name__)

def get_active_session(context: ContextTypes.DEFAULT_TYPE, sid: int):
    if not sid or sid not in acp_service.active_processes:
        return None
    return acp_service.active_processes[sid]

def build_models_keyboard(sid: int, available_models: list, current_model_id: str, page: int = 0) -> tuple[list, str]:
    """Builds an inline keyboard for model selection with pagination."""
    items_per_page = 5
    total_count = len(available_models)
    total_pages = max(1, (total_count + items_per_page - 1) // items_per_page)
    
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    
    keyboard = []
    text = "🤖 *Available Models*\n\n"
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_models = available_models[start_idx:end_idx]
    
    for m in page_models:
        model_id = m.model_id if hasattr(m, 'model_id') else m
        model_name = m.name if hasattr(m, 'name') else str(m)
        status = "✅" if model_id == current_model_id else "⚪"
        text += f"{status} `{escape_markdown(model_name)}`\n"
        keyboard.append([InlineKeyboardButton(f"{status} {model_name}", callback_data=("set_model", sid, model_id, page))])
    
    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=("more_models", sid, page - 1)))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ More", callback_data=("more_models", sid, page + 1)))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    text += f"\n_Page {page + 1}/{total_pages} ({total_count} models)_"
    
    return keyboard, text

@register_command("models", "List available models", category="Agent Configuration")
@authorized_only
async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    session = get_active_session(context, sid)
    if not session:
        await safe_reply(update, "No active session to list models for.")
        return

    models = session.acp_session.models
    if not models or not models.available_models:
        await safe_reply(update, "No models available for the current agent.")
        return

    current = models.current_model_id
    available_models = models.available_models
    
    # Build keyboard with pagination
    keyboard, text = build_models_keyboard(sid, available_models, current, 0)
    
    await safe_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@register_command("model", "Switch current model", usage="<model_id>", category="Agent Configuration")
@authorized_only
async def switch_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Usage: `/model <model_id>`", parse_mode="Markdown")
        return

    target_model = context.args[0]
    sid = await get_current_session_id(update, context)
    session = get_active_session(context, sid)

    if not session:
        await safe_reply(update, "No active session.")
        return

    try:
        await session.conn.set_session_model(session_id=session.acp_session.session_id, model_id=target_model)
        if session.acp_session.models:
            session.acp_session.models.current_model_id = target_model
        await safe_reply(update, f"✅ Model switched to `{target_model}`", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Failed to switch model via command")
        await safe_reply(update, f"❌ Error switching model: {e}")

@register_command("modes", "List available modes", category="Agent Configuration")
@authorized_only
async def modes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    session = get_active_session(context, sid)
    if not session:
        await safe_reply(update, "No active session to list modes for.")
        return

    modes = session.acp_session.modes
    if not modes or not modes.available_modes:
        await safe_reply(update, "No modes available for the current agent.")
        return

    current = modes.current_mode_id
    keyboard = []
    text = "⚙️ *Available Modes*\n\n"

    for mode in modes.available_modes:
        mode_id = mode.id if hasattr(mode, 'id') else mode
        mode_name = mode.name if hasattr(mode, 'name') else str(mode)
        status = "✅" if mode_id == current else "⚪"
        text += f"{status} `{escape_markdown(mode_name)}`\n"
        keyboard.append([InlineKeyboardButton(f"{status} {mode_name}", callback_data=("set_mode", sid, mode_id))])

    await safe_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@register_command("mode", "Switch current mode", usage="<mode_id>", category="Agent Configuration")
@authorized_only
async def switch_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Usage: `/mode <mode_id>`", parse_mode="Markdown")
        return

    target_mode = context.args[0]
    sid = await get_current_session_id(update, context)
    session = get_active_session(context, sid)

    if not session:
        await safe_reply(update, "No active session.")
        return

    try:
        await session.conn.set_session_mode(session_id=session.acp_session.session_id, mode_id=target_mode)
        if session.acp_session.modes:
            session.acp_session.modes.current_mode_id = target_mode
        await safe_reply(update, f"✅ Mode switched to `{target_mode}`", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Failed to switch mode via command")
        await safe_reply(update, f"❌ Error switching mode: {e}")

@router.register("set_model")
async def on_set_model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid, opt_id, page: int = 0):
    query = update.callback_query
    if sid not in acp_service.active_processes:
        await safe_answer(query, "Session not active.")
        return
    session = acp_service.active_processes[sid]
    try:
        await session.conn.set_session_model(
            session_id=session.acp_session.session_id, model_id=opt_id
        )
        session.acp_session.models.current_model_id = opt_id
        # Refresh keyboard with updated model and same page
        keyboard, text = build_models_keyboard(sid, session.acp_session.models.available_models, opt_id, int(page))
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        await safe_answer(query, f"Model switched to {opt_id}")
    except Exception as e:
        logger.exception("Failed to switch model")
        await safe_answer(query, f"Error switching model: {e}", show_alert=True)

@router.register("more_models")
async def on_more_models_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid, page: int):
    query = update.callback_query
    if sid not in acp_service.active_processes:
        await safe_answer(query, "Session not active.")
        return
    session = acp_service.active_processes[sid]
    models = session.acp_session.models
    if not models or not models.available_models:
        await safe_answer(query, "No models available.", show_alert=True)
        return
    
    keyboard, text = build_models_keyboard(sid, models.available_models, models.current_model_id, int(page))
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@router.register("set_mode")
async def on_set_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid, opt_id):
    query = update.callback_query
    if sid not in acp_service.active_processes:
        await safe_answer(query, "Session not active.")
        return
    session = acp_service.active_processes[sid]
    try:
        await session.conn.set_session_mode(
            session_id=session.acp_session.session_id, mode_id=opt_id
        )
        session.acp_session.modes.current_mode_id = opt_id
        modes = session.acp_session.modes
        keyboard = []
        for mode in modes.available_modes:
            mode_id = mode.id if hasattr(mode, "id") else mode
            mode_name = mode.name if hasattr(mode, "name") else str(mode)
            status = "✅" if mode_id == modes.current_mode_id else "⚪"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{status} {mode_name}",
                        callback_data=("set_mode", sid, mode_id),
                    )
                ]
            )
        await safe_edit_reply_markup(query, InlineKeyboardMarkup(keyboard))
        await safe_answer(query, f"Mode switched to {opt_id}")
    except Exception as e:
        logger.exception("Failed to switch mode")
        await safe_answer(query, f"Error switching mode: {e}", show_alert=True)
