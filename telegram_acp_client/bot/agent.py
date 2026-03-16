import asyncio
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, InvalidCallbackData
from acp import text_block
from acp.schema import PermissionOption, FileEditToolCallContent

from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.formatting import format_diff, escape_markdown, is_approval_option
from telegram_acp_client.bot.messaging import (
    send_safe_message, typing_action, send_split_diff, safe_reply, safe_edit, safe_answer
)
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.acp_service import acp_service, TelegramGeminiClient
from telegram_acp_client.services.terminal_service import terminal_service
from telegram_acp_client.bot.streamer import MessageStreamer

logger = logging.getLogger(__name__)

async def start_agent_service(update, context, db_id, path):
    chat_id = update.effective_chat.id

    async def close_stream():
        session = acp_service.active_processes.get(db_id)
        if session and session.streamer:
            await session.streamer.close()
            session.streamer = None

    async def on_text(text):
        session = acp_service.active_processes.get(db_id)
        if not session: return

        if session.streamer and session.streamer.role != "agent":
            await close_stream()

        if session.streamer is None:
            session.streamer = MessageStreamer(context, chat_id, db_id, role="agent")
            await session.streamer.start()

        await session.streamer.add_text(text)

    async def on_permission(tool_call, options):
        await close_stream()
        future = asyncio.Future()
        tc_id = getattr(tool_call, "tool_call_id", "unknown")
        logger.info(f"TOOL PERMISSION REQUESTED: {tc_id} ({tool_call.title})")

        session = acp_service.active_processes.get(db_id)
        if not session: return None

        tc_index = len(session.permission_registry) + 1
        tc_idx_str = str(tc_index)
        session.permission_registry[tc_idx_str] = {"future": future, "full_id": tc_id}

        diff_text = ""
        if hasattr(tool_call, "content") and tool_call.content:
            for content_item in tool_call.content:
                if isinstance(content_item, FileEditToolCallContent):
                    diff_text = format_diff(content_item.old_text, content_item.new_text, content_item.path)
                    break

        btns = []
        for opt in options:
            emoji = "✅" if is_approval_option(opt.name) else "❌"
            btns.append([InlineKeyboardButton(f"{emoji} {opt.name}", callback_data=("perm", db_id, tc_idx_str, opt.option_id))])

        safe_title = escape_markdown(tool_call.title)
        
        # If the title itself is massive (e.g. a huge shell command), truncate it for the prompt
        prompt_title = safe_title
        if len(prompt_title) > 3000:
            prompt_title = prompt_title[:3000] + "... [truncated]"
            # Optionally send the full title as a separate message first
            await send_safe_message(context, chat_id, f"📋 *Full Tool Request Details:*\n`{safe_title}`")

        if diff_text:
            await send_split_diff(context, chat_id, diff_text)

        logger.info(f"SENDING PERMISSION PROMPT for {tc_id}")
        try:
            await send_safe_message(context, chat_id, f"🔐 *Permission Requested:*\n{prompt_title}", reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
        except Exception:
            await send_safe_message(context, chat_id, f"Permission Requested:\n{tool_call.title[:3000]}", reply_markup=InlineKeyboardMarkup(btns))

        try:
            result = await asyncio.wait_for(future, timeout=3600)
            logger.info(f"TOOL PERMISSION RESOLVED: {tc_id} -> {'GRANTED' if result else 'DENIED'}")
            return result
        except asyncio.TimeoutError:
            if not future.done(): future.set_result(None)
            await send_safe_message(context, chat_id, f"⏳ *Timed out:* Permission for `{safe_title}` was automatically denied.")
            return None
        finally:
            session.permission_registry.pop(tc_idx_str, None)

    async def on_tool_start(tool_call):
        await close_stream()
        tc_id = getattr(tool_call, 'tool_call_id', 'unknown')
        logger.info(f"TOOL STARTING: {tc_id} ({tool_call.title})")
        await send_safe_message(context, chat_id, f"🔧 *Tool:* {escape_markdown(tool_call.title)}")

    async def on_thought(thought):
        session = acp_service.active_processes.get(db_id)
        if not session: return

        if session.streamer and session.streamer.role != "thought":
            await close_stream()

        if session.streamer is None:
            session.streamer = MessageStreamer(context, chat_id, db_id, prefix="💭 ", role="thought")
            await session.streamer.start()

        await session.streamer.add_text(thought)
    async def on_system_notification(text): await send_safe_message(context, chat_id, text)

    async def on_terminal_request(command, args, cwd_override):
        await close_stream()
        full_cmd = command if not args else f"{command} " + " ".join(args)
        session_info = await db_service.get_session(db_id)
        current_cwd = cwd_override or terminal_service.get_cwd(chat_id, session_info[1])
        async def send_log(log_msg): await send_safe_message(context, chat_id, log_msg, parse_mode='Markdown')
        return await terminal_service.run_shell(chat_id, full_cmd, current_cwd, send_log, session_id=db_id)

    client = TelegramGeminiClient(on_text, on_permission, on_tool_start, on_thought, on_system_notification, on_terminal_request)
    
    async with typing_action(context, chat_id):
        await acp_service.start_session(db_id, path, client)
        context.user_data["current_session_id"] = db_id

        try:
            files = [f for f in os.listdir(path) if not f.startswith(".")]
            files_list = "\n".join([f"📄 {f}" for f in files]) if files else "_Empty directory_"
        except Exception as e:
            files_list = f"_Error listing files: {str(e)}_"

    await send_safe_message(context, chat_id, f"✅ *Session Active*\n📁 *Path:* `{path}`\n\n*Files:*\n{files_list}")

@authorized_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, text = update.effective_chat.id, update.message.text
    sid = context.user_data.get("current_session_id") or await db_service.get_last_session_id(chat_id)
    if sid: context.user_data["current_session_id"] = sid
    if not sid:
        await safe_reply(update, "Select a session first via /sessions or create /new.")
        return

    if sid not in acp_service.active_processes:
        session_info = await db_service.get_session(sid)
        if session_info:
            await safe_reply(update, "🔄 Bot restarted. Reconnecting to agent...")
            await start_agent_service(update, context, sid, session_info[1])
        else:
            await safe_reply(update, "Session not found.")
            return

    await db_service.save_message(sid, "user", text)
    session = acp_service.active_processes[sid]
    if session.is_busy:
        await safe_reply(update, "⏳ *Agent is working*, please wait...", parse_mode='Markdown')
        return

    async def run_prompt():
        session.is_busy = True
        try:
            async with typing_action(context, chat_id):
                await session.conn.prompt(session_id=session.acp_session.session_id, prompt=[text_block(text)])
        except Exception as e:
            logger.exception("Error during agent prompt")
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
    await safe_answer(query, )
    chat_id = update.effective_chat.id if update.effective_chat else 0

    if isinstance(data, tuple):
        action = data[0]
        if action == "switch":
            sid = data[1]
            context.user_data["current_session_id"] = sid
            await db_service.set_last_session(chat_id, sid)
            if sid not in acp_service.active_processes:
                session_info = await db_service.get_session(sid)
                if session_info: await start_agent_service(update, context, sid, session_info[1])
            await safe_edit(query, f"Switched to Session ID: {sid}")
        elif action == "perm":
            _, target_db_id, tc_idx, opt_id = data
            session = acp_service.active_processes.get(target_db_id)
            if session and tc_idx in session.permission_registry:
                reg = session.permission_registry.pop(tc_idx)
                future = reg["future"]
                
                # Preserving original details, ensuring they don't exceed Telegram limit on edit
                original_text = query.message.text if query.message else "Permission Prompt"
                if len(original_text) > 3500:
                    original_text = original_text[:3500] + "... [truncated]"
                
                if is_approval_option(opt_id):
                    if not future.done():
                        from acp.schema import PermissionOption
                        future.set_result(PermissionOption(option_id=opt_id, name="Allowed", kind="allow_once"))
                    await safe_edit(query, f"{original_text}\n\n✅ *Granted*", parse_mode='Markdown')
                else:
                    if not future.done(): future.set_result(None)
                    try:
                        await session.conn.cancel(session_id=session.acp_session.session_id)
                        await safe_edit(query, f"{original_text}\n\n❌ *Task Stopped & Permission Denied*", parse_mode='Markdown')
                    except Exception: 
                        await safe_edit(query, f"{original_text}\n\n❌ *Permission Denied*", parse_mode='Markdown')
            else: await safe_edit(query, "⚠️ Request expired.")
