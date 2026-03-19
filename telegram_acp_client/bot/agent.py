import asyncio
import logging
import os
import base64
import io

from acp import text_block, image_block, audio_block
from acp.schema import FileEditToolCallContent
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, InvalidCallbackData

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
from telegram_acp_client.bot.streamer import MessageStreamer
from telegram_acp_client.services.acp_service import TelegramGeminiClient, acp_service
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.terminal_service import terminal_service

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


async def start_agent_service(update, context, db_id, path):
    chat_id = update.effective_chat.id

    async def close_stream():
        session = acp_service.active_processes.get(db_id)
        if session and session.streamer:
            await session.streamer.close()
            session.streamer = None

    async def on_text(text):
        session = acp_service.active_processes.get(db_id)
        if not session:
            return

        if session.streamer and session.streamer.role != "agent":
            await close_stream()

        if session.streamer is None:
            session.streamer = MessageStreamer(context, chat_id, db_id, role="agent")
            await session.streamer.start()

        await session.streamer.add_text(text)

    def format_tool_title(tool_call, ri, kind_val=None):
        title = tool_call.title
        path = ri.get("path") or ri.get("file_path") or ri.get("filePath") or ri.get("filepath")
        
        # 1. Start with escaped title
        safe_title = escape_markdown(title)
        
        # 2. Add path if present and not already in title
        if path and path not in title:
            safe_title += f": `{escape_markdown(str(path))}`"
            
        # 3. Add command for execute/bash tools
        kind = kind_val or getattr(tool_call, "kind", "other")
        if hasattr(kind, "value"): kind = kind.value
        
        if kind == "execute" or "bash" in title.lower() or "shell" in title.lower():
            cmd = ri.get("command") or ri.get("cmd") or ri.get("script") or ri.get("code")
            if cmd:
                safe_title += f"\n\n`{escape_markdown(str(cmd))}`"
        
        return safe_title

    async def on_permission(tool_call, options):
        await close_stream()
        future = asyncio.Future()
        tc_id = getattr(tool_call, "tool_call_id", "unknown")
        logger.info(f"TOOL PERMISSION REQUESTED: {tc_id} ({tool_call.title})")

        session = acp_service.active_processes.get(db_id)
        if not session:
            return None

        # Use tracked state if available to complement the tool_call object
        state = session.tool_calls.get(tc_id, {})
        
        tc_index = len(session.permission_registry) + 1
        tc_idx_str = str(tc_index)
        # Store option names mapping to reliably detect approval keywords in callback handler
        session.permission_registry[tc_idx_str] = {
            "future": future,
            "full_id": tc_id,
            "options": {opt.option_id: opt.name for opt in options},
        }

        # Merge raw_input from state into a local ri dict for extraction
        ri = getattr(tool_call, "raw_input", {})
        if not isinstance(ri, dict): ri = {}
        tracked_ri = state.get("raw_input", {})
        if isinstance(tracked_ri, dict):
            # Tracked data takes precedence as it might be newer (e.g. from Progress update)
            ri = {**ri, **tracked_ri}
        
        logger.debug(f"Merged raw_input for {tc_id}: {ri}")

        diff_text = ""
        # Check both the provided tool_call and the tracked state for content
        content_sources = []
        if hasattr(tool_call, "content") and tool_call.content: content_sources.append(tool_call.content)
        if state.get("content"): content_sources.append(state["content"])

        for content_list in content_sources:
            if diff_text: break
            for content_item in content_list:
                c_type = getattr(content_item, "type", None) or (
                    content_item.get("type") if isinstance(content_item, dict) else None
                )
                if c_type == "diff":
                    old_txt = getattr(content_item, "oldText", getattr(content_item, "old_text", "")) or (content_item.get("oldText") if isinstance(content_item, dict) else "")
                    new_txt = getattr(content_item, "newText", getattr(content_item, "new_text", "")) or (content_item.get("newText") if isinstance(content_item, dict) else "")
                    path = getattr(content_item, "path", "") or (content_item.get("path") if isinstance(content_item, dict) else "")
                    diff_text = format_diff(str(old_txt or ""), str(new_txt or ""), str(path))
                    break

        # Fallback to merged raw_input if diff_text is still empty
        if not diff_text and ri:
            path = (
                ri.get("path")
                or ri.get("file_path")
                or ri.get("filePath")
                or ri.get("filepath")
                or "unknown_file"
            )

            if "diff" in ri:
                diff_text = str(ri.get("diff"))
                lines = diff_text.splitlines()
                clean_lines = [l for l in lines if not (l.startswith("Index: ") or l.startswith("======"))]
                diff_text = "\n".join(clean_lines).strip()
            else:
                old = ri.get("oldText") or ri.get("old_str") or ri.get("oldString") or ri.get("old_string") or ri.get("old_text")
                new = ri.get("newText") or ri.get("new_str") or ri.get("newString") or ri.get("new_string") or ri.get("new_text")

                if old is not None and new is not None:
                    diff_text = format_diff(str(old), str(new), path)
                elif "content" in ri:
                    diff_text = format_diff("", str(ri.get("content", "")), path)
                elif "file_text" in ri:
                    diff_text = format_diff("", str(ri.get("file_text", "")), path)
                elif "text" in ri:
                    diff_text = format_diff("", str(ri.get("text", "")), path)

        btns = []
        for opt in options:
            o_kind = getattr(opt, "kind", None)
            if hasattr(o_kind, "value"): o_kind = o_kind.value

            if o_kind in ["allow_once", "allow_always"]:
                emoji = "✅"
            elif o_kind in ["reject_once", "reject_always"]:
                emoji = "❌"
            else:
                emoji = "✅" if is_approval_option(opt.name) else "❌"
            btns.append([InlineKeyboardButton(f"{emoji} {opt.name}", callback_data=("perm", db_id, tc_idx_str, opt.option_id))])

        # Format title with path and command
        safe_title = format_tool_title(tool_call, ri)

        # If the title itself is massive (e.g. a huge shell command), truncate it for the prompt
        prompt_title = safe_title
        if len(prompt_title) > 3000:
            prompt_title = prompt_title[:3000] + "... [truncated]"
            # Optionally send the full title as a separate message first
            await send_safe_message(
                context, chat_id, f"📋 *Full Tool Request Details:*\n`{safe_title}`"
            )

        if diff_text:
            await send_split_diff(context, chat_id, diff_text)

        logger.info(f"SENDING PERMISSION PROMPT for {tc_id}")
        msg_obj = None
        try:
            msg_obj = await send_safe_message(
                context,
                chat_id,
                f"🔐 *Permission Requested:*\n{prompt_title}",
                reply_markup=InlineKeyboardMarkup(btns),
                parse_mode="Markdown",
            )
        except Exception:
            msg_obj = await send_safe_message(
                context,
                chat_id,
                f"Permission Requested:\n{tool_call.title[:3000]}",
                reply_markup=InlineKeyboardMarkup(btns),
            )

        if msg_obj:
            session.permission_messages[tc_id] = (msg_obj, options)

        try:
            result = await asyncio.wait_for(future, timeout=3600)
            logger.info(
                f"TOOL PERMISSION RESOLVED: {tc_id} -> {'GRANTED' if result else 'DENIED'}"
            )
            return result
        except TimeoutError:
            if not future.done():
                future.set_result(None)
            await send_safe_message(
                context,
                chat_id,
                f"⏳ *Timed out:* Permission for `{safe_title}` was automatically denied.",
            )
            return None
        finally:
            session.permission_registry.pop(tc_idx_str, None)
            session.permission_messages.pop(tc_id, None)

    async def on_permission_update(tc_id, tool_call, msg_obj, options):
        # Merge raw_input from potential tracked state
        session = acp_service.active_processes.get(db_id)
        ri = getattr(tool_call, "raw_input", {})
        if not isinstance(ri, dict): ri = {}
        
        if session:
            state = session.tool_calls.get(tc_id, {})
            tracked_ri = state.get("raw_input", {})
            if isinstance(tracked_ri, dict):
                ri = {**ri, **tracked_ri}

        safe_title = format_tool_title(tool_call, ri)
        await safe_edit(msg_obj, f"🔐 *Permission Requested:*\n{safe_title}", parse_mode="Markdown")

    async def on_tool_start(tool_call):
        await close_stream()
        tc_id = getattr(tool_call, "tool_call_id", "unknown")
        logger.info(f"TOOL STARTING: {tc_id} ({tool_call.title})")

        kind = getattr(tool_call, "kind", "other")
        if hasattr(kind, "value"):
            kind = kind.value
        emoji = KIND_EMOJIS.get(kind, "🔧")

        return await send_safe_message(
            context, chat_id, f"{emoji} *Tool:* {escape_markdown(tool_call.title)}"
        )

    async def on_tool_update(tc_id, text, msg_obj):
        if not msg_obj:
            return
        await safe_edit(msg_obj, text, parse_mode="Markdown")

    async def on_thought(thought):
        session = acp_service.active_processes.get(db_id)
        if not session:
            return

        if session.streamer and session.streamer.role != "thought":
            await close_stream()

        if session.streamer is None:
            session.streamer = MessageStreamer(
                context, chat_id, db_id, prefix="💭 ", role="thought"
            )
            await session.streamer.start()

        await session.streamer.add_text(thought)

    async def on_system_notification(text):
        await send_safe_message(context, chat_id, text)

    async def on_terminal_request(command, args, cwd_override):
        await close_stream()
        full_cmd = command if not args else f"{command} " + " ".join(args)
        session_info = await db_service.get_session(db_id)
        current_cwd = cwd_override or terminal_service.get_cwd(chat_id, session_info[1])

        async def send_log(log_msg):
            await send_safe_message(context, chat_id, log_msg, parse_mode="Markdown")

        return await terminal_service.run_shell(
            chat_id, full_cmd, current_cwd, send_log, session_id=db_id
        )

    client = TelegramGeminiClient(
        on_text,
        on_permission,
        on_tool_start,
        on_thought,
        on_system_notification,
        on_terminal_request,
        on_tool_update=on_tool_update,
        on_permission_update=on_permission_update,
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
                    "✅" if mode_id == getattr(modes, "current_mode_id", None) else "⚪"
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
    )


@authorized_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sid = context.user_data.get(
        "current_session_id"
    ) or await db_service.get_last_session_id(chat_id)
    if sid:
        context.user_data["current_session_id"] = sid
    if not sid:
        await safe_reply(update, "Select a session first via /sessions or create /new.")
        return

    if sid not in acp_service.active_processes:
        session_info = await db_service.get_session(sid)
        if session_info:
            await safe_reply(update, "🔄 Bot restarted. Reconnecting to agent...")
            await start_agent_service(update, context, sid, session_info[1])
            if sid not in acp_service.active_processes:
                return
        else:
            await safe_reply(update, "Session not found.")
            return

    session = acp_service.active_processes[sid]

    # Health Check (Disabled for now)
    # if not session.is_alive:
    #     logger.warning(f"Session {sid} is dead. Attempting restart.")
    #     session_info = await db_service.get_session(sid)
    #     if session_info:
    #         await safe_reply(update, "⚠️ Agent session crashed. Restarting...")
    #         await start_agent_service(update, context, sid, session_info[1])
    #         session = acp_service.active_processes.get(sid)
    #         if not session: return
    #     else:
    #         await safe_reply(update, "❌ Session lost.")
    #         return

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
                        context, chat_id, icon_msg, parse_mode="Markdown"
                    )
        except Exception as e:
            logger.exception("Error during agent prompt")
            if "LimitOverrunError" in str(e) or "connection" in str(e).lower():
                await acp_service.stop_session(sid)
            await send_safe_message(context, chat_id, f"❌ Agent Error: {e}")
        finally:
            if session.streamer:
                await session.streamer.close()
                session.streamer = None
            session.is_busy = False

    asyncio.create_task(run_prompt())


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    if data is InvalidCallbackData:
        await safe_answer(query, "This button is no longer valid.", show_alert=True)
        return
    await safe_answer(
        query,
    )
    chat_id = update.effective_chat.id if update.effective_chat else 0

    if isinstance(data, tuple):
        action = data[0]
        if action == "switch":
            sid = data[1]
            context.user_data["current_session_id"] = sid
            await db_service.set_last_session(chat_id, sid)
            if sid not in acp_service.active_processes:
                session_info = await db_service.get_session(sid)
                if session_info:
                    await start_agent_service(update, context, sid, session_info[1])
            await safe_edit(query, f"Switched to Session ID: {sid}")
        elif action == "perm":
            _, target_db_id, tc_idx, opt_id = data
            session = acp_service.active_processes.get(target_db_id)
            if session and tc_idx in session.permission_registry:
                reg = session.permission_registry.pop(tc_idx)
                future = reg["future"]
                options_map = reg.get("options", {})

                # Check both the ID and the human-readable name for approval keywords
                opt_name = options_map.get(opt_id, opt_id)
                is_approved = is_approval_option(opt_name) or is_approval_option(opt_id)

                # Preserving original details, ensuring they don't exceed Telegram limit on edit
                original_text = (
                    query.message.text if query.message else "Permission Prompt"
                )
                if len(original_text) > 3500:
                    original_text = original_text[:3500] + "... [truncated]"

                if is_approved:
                    if not future.done():
                        from acp.schema import PermissionOption

                        future.set_result(
                            PermissionOption(
                                option_id=opt_id, name="Allowed", kind="allow_once"
                            )
                        )
                    await safe_edit(
                        query, f"{original_text}\n\n✅ *Granted*", parse_mode="Markdown"
                    )
                else:
                    if not future.done():
                        future.set_result(None)
                    try:
                        await session.conn.cancel(
                            session_id=session.acp_session.session_id
                        )
                        await safe_edit(
                            query,
                            f"{original_text}\n\n❌ *Task Stopped & Permission Denied*",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        await safe_edit(
                            query,
                            f"{original_text}\n\n❌ *Permission Denied*",
                            parse_mode="Markdown",
                        )
            else:
                await safe_edit(query, "⚠️ Request expired.")
        elif action == "set_model":
            _, sid, opt_id = data
            if sid not in acp_service.active_processes:
                await safe_answer(query, "Session not active.")
                return
            session = acp_service.active_processes[sid]
            try:
                await session.conn.set_session_model(
                    session_id=session.acp_session.session_id, model_id=opt_id
                )
                session.acp_session.models.current_model_id = opt_id
                await safe_answer(query, f"Model switched to {opt_id}")
            except Exception as e:
                logger.exception("Failed to switch model")
                await safe_answer(query, f"Error switching model: {e}", show_alert=True)
        elif action == "set_mode":
            _, sid, opt_id = data
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
