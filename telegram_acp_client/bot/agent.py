import asyncio
import logging
import os
import base64
import io

from acp import text_block, image_block, audio_block
from acp.schema import FileEditToolCallContent
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, InvalidCallbackData

from telegram_acp_client.bot.callback_router import router
from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.formatting import (
    escape_markdown,
    format_diff,
    is_approval_option,
)
from telegram_acp_client.bot.messaging import (
    safe_answer,
    safe_edit,
    safe_edit_reply_markup,
    safe_reply,
    send_safe_message,
    send_split_diff,
    typing_action,
)
from telegram_acp_client.bot.nodes.permission import PermissionNode
from telegram_acp_client.bot.streamer import MessageStreamer
from telegram_acp_client.bot.session_streamer import SessionDraftStreamer
from telegram_acp_client.bot.ui import (
    create_node,
    format_interaction_title,
)
from telegram_acp_client.services.acp_service import TelegramGeminiClient, acp_service
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.entities import ToolEntity
from telegram_acp_client.services.terminal_service import terminal_service
from telegram_acp_client.bot.threads import (
    extract_thread_id,
    get_current_session_id,
    set_current_session_id,
)

logger = logging.getLogger(__name__)

KIND_EMOJIS = {
    "read": "📖",
    "edit": "📝",
    "delete": "🗑️",
    "move": "📦",
    "search": "🔍",
    "execute": "⚙️",
    "think": "💭",
    "fetch": "🌐",
    "other": "🔧",
}


async def start_agent_service(update, context, db_id, path, thread_id=None):
    chat_id = update.effective_chat.id

    async def on_entity_change(session_id, entity_id, kind, role="agent", prefix=""):
        session = acp_service.active_processes.get(db_id)
        if not session:
            return

        # Initialize session-level draft streamer if not already present
        if not session.draft_streamer:
            session.draft_streamer = SessionDraftStreamer(
                context, chat_id, db_id, thread_id
            )

        # Use the new Node-based architecture for all interactions
        if entity_id not in session.nodes:
            session.nodes[entity_id] = create_node(
                context,
                chat_id,
                db_id,
                entity_id,
                kind,
                thread_id,
                role=role,
                prefix=prefix,
                streamer=session.draft_streamer,  # Pass the session streamer
            )

    async def on_entity_finished(session_id, entity_id):
        session = acp_service.active_processes.get(db_id)
        if not session:
            return

        # Finalize the Node and remove it
        node = session.nodes.pop(entity_id, None)
        if node:
            await node.finalize()

    async def close_stream():
        session = acp_service.active_processes.get(db_id)
        if not session:
            return
        
        # Finalize the session streamer itself
        if session.draft_streamer:
            await session.draft_streamer.close()

        # Finalize all active nodes
        for node in list(session.nodes.values()):
            await node.finalize()
        session.nodes.clear()

    async def on_tool_start(tool_call):
        await close_stream()
        tc_id = getattr(tool_call, "tool_call_id", "unknown")
        logger.info(f"TOOL START: {tc_id} ({tool_call.title})")

    async def on_permission(tool_call, options):
        await close_stream()
        tc_id = getattr(tool_call, "tool_call_id", "unknown")
        logger.info(f"TOOL PERMISSION REQUESTED: {tc_id} ({tool_call.title})")

        session = acp_service.active_processes.get(db_id)
        if not session:
            return None

        # Ensure the tool Node exists and is up to date for data merging
        if tc_id not in session.nodes:
            await on_entity_change(session.acp_session.session_id, tc_id, "tool")

        node = session.nodes[tc_id]
        node.update(tool_call)

        # Create and register a session-local PermissionNode
        session.perm_counter += 1
        tc_idx = str(session.perm_counter)
        perm_node = PermissionNode(
            context, chat_id, db_id, tc_id, tc_idx, options, thread_id
        )
        session.permission_nodes[tc_idx] = perm_node

        # Format title and delegate rendering
        safe_title = format_interaction_title(
            tool_call.title, node.raw_input, node.tool_kind
        )
        await perm_node.render(safe_title, tool_call, node)

        # Wait for user interaction
        result = await perm_node.future

        # Cleanup and return
        session.permission_nodes.pop(tc_idx, None)
        return result

    async def on_permission_update(tc_id, tool_call, msg_obj, options):
        session = acp_service.active_processes.get(db_id)
        if not session:
            return

        # Find the active permission node for this tool call
        perm_node = next(
            (n for n in session.permission_nodes.values() if n.tc_id == tc_id), None
        )
        if perm_node:
            node = session.nodes.get(tc_id)
            safe_title = format_interaction_title(
                tool_call.title, node.raw_input, node.tool_kind
            )
            await perm_node.render(safe_title, tool_call, node)

    async def on_system_notification(text):
        await send_safe_message(context, chat_id, text, message_thread_id=thread_id)

    async def on_terminal_request(command, args, cwd_override):
        await close_stream()
        full_cmd = command if not args else f"{command} " + " ".join(args)
        session_info = await db_service.get_session(db_id)
        current_cwd = cwd_override or terminal_service.get_cwd(chat_id, session_info[1])

        async def send_log(log_msg):
            await send_safe_message(
                context,
                chat_id,
                log_msg,
                parse_mode="Markdown",
                message_thread_id=thread_id,
            )

        return await terminal_service.run_shell(
            chat_id, full_cmd, current_cwd, send_log, session_id=db_id
        )

    client = TelegramGeminiClient(
        on_permission=on_permission,
        on_tool_start=on_tool_start,
        on_system_notification=on_system_notification,
        on_terminal_request=on_terminal_request,
        on_tool_update=lambda *args: None,
        on_permission_update=on_permission_update,
        on_entity_change=on_entity_change,
        on_entity_finished=on_entity_finished,
    )

    async with typing_action(context, chat_id):
        await acp_service.start_session(db_id, path, client)
        context.user_data["current_session_id"] = db_id

        try:
            files = [f for f in os.listdir(path) if not f.startswith(".")]
            files_list = (
                "\n".join([f"📄 {escape_markdown(f)}" for f in files])
                if files
                else "_Empty directory_"
            )
        except Exception as e:
            files_list = f"_Error listing files: {escape_markdown(str(e))}_"

    session = acp_service.active_processes.get(db_id)
    models_info = ""
    keyboard = []

    if session and session.acp_session:
        models = getattr(session.acp_session, "models", None)
        if models and getattr(models, "current_model_id", None):
            models_info = f"\n🤖 *Model:* `{escape_markdown(models.current_model_id)}`"

        modes = getattr(session.acp_session, "modes", None)
        if (
            modes
            and getattr(modes, "available_modes", None)
            and len(modes.available_modes) > 1
        ):
            models_info += "\n⚙️ *Available Modes:*"
            for mode in modes.available_modes:
                mode_id = mode.id if hasattr(mode, "id") else mode
                mode_name = mode.name if hasattr(mode, "name") else str(mode)
                status = (
                    "✅"
                    if mode_id == getattr(modes, "current_model_id", None)
                    else "⚪"
                )
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"{status} {mode_name}",
                            callback_data=("set_mode", db_id, mode_id),
                        )
                    ]
                )

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    await send_safe_message(
        context,
        chat_id,
        f"✅ *Session Active*\n📁 *Path:* `{escape_markdown(path)}`{models_info}\n\n*Files:*\n{files_list}",
        reply_markup=reply_markup,
        parse_mode="Markdown",
        message_thread_id=thread_id,
    )


@authorized_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)

    pending_name_key = f"pending_new_session_{thread_id}_name"
    pending_path_key = f"pending_new_session_{thread_id}_path"

    if (
        context.user_data.get(pending_name_key) is not None
        and context.user_data.get(pending_path_key) is None
    ):
        session_name = update.message.text.strip()
        context.user_data[pending_name_key] = session_name
        context.user_data[pending_path_key] = ""
        await safe_reply(
            update,
            f"📁 Session name: `{escape_markdown(session_name)}`\n\nEnter the path for the session (e.g., `/path/to/project`):",
        )
        return

    if (
        context.user_data.get(pending_path_key) is not None
        and context.user_data.get(pending_name_key) is not None
    ):
        path = update.message.text.strip()
        session_name = context.user_data.pop(pending_name_key)
        context.user_data.pop(pending_path_key)

        if not path:
            await safe_reply(update, "Path cannot be empty. Please enter a valid path:")
            return

        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            await safe_reply(update, f"❌ Invalid path: {e}")
            return

        sid = await db_service.create_session(chat_id, thread_id, session_name, path)
        await set_current_session_id(update, context, sid, thread_id)
        await safe_reply(
            update, f"✅ Session `{escape_markdown(session_name)}` created!"
        )
        await start_agent_service(update, context, sid, path, thread_id)
        return

    sid = await get_current_session_id(update, context)

    if not sid:
        if thread_id:
            available_sessions = await db_service.get_sessions_for_thread(chat_id)
            buttons = []
            for s_id, s_name, s_path in available_sessions:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"📁 {escape_markdown(s_name)}",
                            callback_data=("link_session", s_id, thread_id),
                        )
                    ]
                )
            buttons.append(
                [
                    InlineKeyboardButton(
                        "➕ Create New Session",
                        callback_data=("new_session", thread_id),
                    )
                ]
            )
            await safe_reply(
                update,
                "This topic has no active session. Choose one below or create a new session:",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        else:
            await safe_reply(
                update, "Select a session first via /sessions or create /new."
            )
        return

    if sid not in acp_service.active_processes:
        session_info = await db_service.get_session(sid)
        if session_info:
            await safe_reply(update, "🔄 Bot restarted. Reconnecting to agent...")
            await start_agent_service(update, context, sid, session_info[1], thread_id)
            if sid not in acp_service.active_processes:
                return
        else:
            await safe_reply(update, "Session not found.")
            return

    session = acp_service.active_processes[sid]

    if session.is_busy:
        await safe_reply(
            update, "⏳ *Agent is working*, please wait...", parse_mode="Markdown"
        )
        return

    # Extract multi-modal content
    prompt_blocks = []
    log_text = ""

    # 1. Handle Text
    text = update.message.text or update.message.caption
    if text:
        prompt_blocks.append(text_block(text))
        log_text += text

    # 2. Handle Photo
    if update.message.photo:
        async with typing_action(context, chat_id):
            photo_file = await update.message.photo[-1].get_file()
            buf = io.BytesIO()
            await photo_file.download_to_memory(buf)
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            prompt_blocks.append(image_block(data=img_b64, mime_type="image/jpeg"))
            log_text += " [Image]"

    # 3. Handle Voice/Audio
    if update.message.voice:
        async with typing_action(context, chat_id):
            voice_file = await update.message.voice.get_file()
            buf = io.BytesIO()
            await voice_file.download_to_memory(buf)
            audio_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            prompt_blocks.append(
                audio_block(
                    data=audio_b64,
                    mime_type=update.message.voice.mime_type or "audio/ogg",
                )
            )
            log_text += " [Voice]"

    if not prompt_blocks:
        return

    await db_service.save_message(sid, "user", log_text.strip())

    async def run_prompt():
        session.is_busy = True
        try:
            async with typing_action(context, chat_id):
                resp = await session.conn.prompt(
                    session_id=session.acp_session.session_id, prompt=prompt_blocks
                )
                stop_reason = getattr(resp, "stop_reason", "unknown")
                logger.info(f"PROMPT COMPLETED: {stop_reason}")

                if stop_reason != "end_turn":
                    reason_icons = {
                        "max_tokens": "⚠️ *Max tokens reached*",
                        "max_turn_requests": "⚠️ *Max turn requests exceeded*",
                        "refusal": "🚫 *Agent refused*",
                        "cancelled": "⏹️ *Task cancelled*",
                    }
                    icon_msg = reason_icons.get(
                        stop_reason, f"💡 *Turn stopped:* `{stop_reason}`"
                    )
                    await send_safe_message(
                        context,
                        chat_id,
                        icon_msg,
                        parse_mode="Markdown",
                        message_thread_id=thread_id,
                    )
        except Exception as e:
            logger.exception("Error during agent prompt")
            if "LimitOverrunError" in str(e) or "connection" in str(e).lower():
                await acp_service.stop_session(sid)
            await send_safe_message(
                context, chat_id, f"❌ Agent Error: {e}", message_thread_id=thread_id
            )
        finally:
            # Entity closing handled via on_entity_finished notifications from service
            session.is_busy = False

    asyncio.create_task(run_prompt())

@router.register("perm")
async def on_perm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target_db_id, tc_idx, opt_id
):
    query = update.callback_query
    session = acp_service.active_processes.get(int(target_db_id))
    if session:
        # tc_idx is the key in session.permission_nodes
        perm_node = session.permission_nodes.get(tc_idx)
        if perm_node:
            await perm_node.handle_click(opt_id, session)
        else:
            await safe_edit(query, "⚠️ Request expired or already processed.")
    else:
        await safe_answer(query, "Session not active.", show_alert=True)


@router.register("link_session")
async def on_link_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid, thread_id):
    query = update.callback_query
    thread_id = int(thread_id) if thread_id else None
    session_info = await db_service.get_session(sid)
    if session_info:
        await set_current_session_id(update, context, sid, thread_id)
        await safe_edit(query, f"🔗 Session linked. Starting agent...")
        await start_agent_service(
            update, context, sid, session_info[1], thread_id
        )
    else:
        await safe_answer(query, "Session not found.", show_alert=True)


@router.register("new_session")
async def on_new_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, thread_id):
    query = update.callback_query
    thread_id = int(thread_id) if thread_id else None
    pending_name_key = f"pending_new_session_{thread_id}_name"
    pending_path_key = f"pending_new_session_{thread_id}_path"
    # We set name to True to indicate we are waiting for the name input
    context.user_data[pending_name_key] = True
    context.user_data[pending_path_key] = None
    await safe_edit(
        query, "📝 *Create New Session*\n\nPlease provide a session name:"
    )
