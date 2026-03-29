import asyncio
import os

from acp import text_block
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    SwitchInlineQueryChosenChat,
)
from telegram.ext import ContextTypes

from telegram_acp_client.bot.agent import start_agent_service
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
from telegram_acp_client.bot.registry import register_command
from telegram_acp_client.bot.threads import (
    extract_thread_id,
    get_current_session_id,
    set_current_session_id,
    clear_current_session_id,
)


@register_command(
    "status", "Show current session status", category="Session Management"
)
@authorized_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    if not sid:
        await safe_reply(update, "No active session in this thread.")
        return
        
    session_info = await db_service.get_session(sid)
    if not session_info:
         await safe_reply(update, "Session not found in database.")
         return
         
    name, path = session_info[0], session_info[1]
    
    if sid not in acp_service.active_processes:
        status_msg = f"⚪ *Session:* `{name}` (Inactive)\n*Path:* `{path}`"
        await safe_reply(update, status_msg, parse_mode="Markdown")
        return
        
    session = acp_service.active_processes[sid]
    is_busy = "⏳ Busy" if session.is_busy else "🟢 Idle"
    nodes_count = len(session.nodes)
    
    active_tools = [n for n in session.nodes.values() if getattr(n, "kind", None) == "tool" and getattr(n, "status", None) in ["in_progress", "pending"]]
    
    status_msg = f"🟢 *Session:* `{name}`\n"
    status_msg += f"*Path:* `{path}`\n"
    status_msg += f"*Status:* {is_busy}\n"
    status_msg += f"*Active Interactions:* {nodes_count}\n"
    
    if active_tools:
        status_msg += "\n*Active Tools:*\n"
        for t in active_tools:
            name_val = getattr(t, "data", {}).get("name", "unknown") if isinstance(getattr(t, "data", None), dict) else "unknown"
            status_msg += f"- `{name_val}` (_{t.status}_)\n"
            
    await safe_reply(update, status_msg, parse_mode="Markdown")


@register_command(
    "new",
    "Create a new agent session",
    usage="<name> <absolute_path>",
    category="Session Management",
)
@authorized_only
async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await safe_reply(update, "Usage: /new <name> <absolute_path>")
        return
    name, path = context.args[0], os.path.abspath(context.args[1])

    thread_id = extract_thread_id(update)
    chat_id = update.effective_chat.id

    if thread_id:
        existing_sid = await get_current_session_id(update, context)
        if existing_sid:
            session_info = await db_service.get_session(existing_sid)
            s_name = session_info[0] if session_info else "Unknown"
            await safe_reply(
                update,
                f"⚠️ This topic is already hard-linked to session `{s_name}`.\nYou must delete it first or create a new topic.",
            )
            return
    else:
        # User is in General chat, prompt them to pick a topic via SwitchInlineQueryChosenChat
        switch_config = SwitchInlineQueryChosenChat(
            query=f" /new {name} {path}",
            allow_group_chats=True,
            allow_user_chats=False,
            allow_bot_chats=False,
            allow_channel_chats=False,
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "🧵 Select Topic to Create Session",
                    switch_inline_query_chosen_chat=switch_config,
                )
            ]
        ]
        await safe_reply(
            update,
            "Sessions should be created inside topics to keep them organized. Click the button below to pick a topic and send the command there:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    os.makedirs(path, exist_ok=True)
    async with typing_action(context, chat_id):
        db_id = await db_service.create_session(chat_id, thread_id, name, path)
        await set_current_session_id(update, context, db_id, thread_id)
        await start_agent_service(update, context, db_id, path, thread_id)


@register_command(
    "sessions", "List active agent sessions", category="Session Management"
)
@authorized_only
async def list_sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = extract_thread_id(update)
    chat_id = update.effective_chat.id

    if thread_id:
        sid = await get_current_session_id(update, context)
        if sid:
            session_info = await db_service.get_session(sid)
            s_name = session_info[0] if session_info else "Unknown"
            status = "🟢" if sid in acp_service.active_processes else "⚪"
            await safe_reply(
                update,
                f"🧵 **Topic Session**\nThis topic is hard-linked to:\n{status} {s_name}\n\n*Session switching is disabled inside topics.*",
                parse_mode="Markdown",
            )
        else:
            await safe_reply(
                update, "This topic has no active session. Use `/new` to create one."
            )
        return

    sessions = await db_service.get_all_sessions(chat_id)
    if not sessions:
        await safe_reply(update, "No sessions. Use /new.")
        return

    keyboard = []
    for sid, name, path, t_id in sessions:
        status = "🟢" if sid in acp_service.active_processes else "⚪"
        if t_id and t_id != 0:
            # We use a URL deep-link here because SwitchInlineQueryChosenChat only opens a generic picker,
            # it cannot programmatically force the user into the *correct* existing topic.
            chat_id_str = str(chat_id)
            if chat_id_str.startswith("-100"):
                chat_id_str = chat_id_str[4:]
            link = f"https://t.me/c/{chat_id_str}/{t_id}"
            keyboard.append([InlineKeyboardButton(f"🧵 {status} {name}", url=link)])
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{status} {name}", callback_data=("switch", sid)
                    )
                ]
            )

    await safe_reply(update, "Workspaces:", reply_markup=InlineKeyboardMarkup(keyboard))


@register_command(
    "restart", "Restart the current session", category="Session Management"
)
@authorized_only
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    if not sid:
        await safe_reply(update, "No active session to restart.")
        return
    session_info = await db_service.get_session(sid)
    if not session_info:
        await safe_reply(update, "Session data not found.")
        return
    name, path = session_info
    await safe_reply(update, f"♻️ Restarting agent for `{name}`...")
    async with typing_action(context, update.effective_chat.id):
        await acp_service.stop_session(sid)
        thread_id = extract_thread_id(update)
        await start_agent_service(update, context, sid, path, thread_id)
        if sid in acp_service.active_processes:
            acp_service.active_processes[sid].is_busy = False
    await safe_reply(update, f"✅ Agent for `{name}` has been restarted.")


@register_command(
    "stop", "Stop the current agent session", category="Session Management"
)
@authorized_only
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    if not sid or sid not in acp_service.active_processes:
        await safe_reply(update, "No active agent session to stop.")
        return
    session = acp_service.active_processes[sid]
    if not session.is_busy:
        await safe_reply(update, "Agent is not currently performing any task.")
        return
    try:
        await session.conn.cancel(session_id=session.acp_session.session_id)
        await safe_reply(update, "⏹️ *Stop signal sent*.", parse_mode="Markdown")
    except Exception as e:
        await safe_reply(update, f"❌ Failed to stop task: {e}")


@register_command(
    "historyinject",
    "Inject history into current session",
    usage="<n>",
    category="Session Management",
)
@authorized_only
async def history_inject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    if not sid:
        await safe_reply(update, "No session linked to this thread.")
        return
    
    # If session exists but isn't active, start it
    if sid not in acp_service.active_processes:
        session_info = await db_service.get_session(sid)
        if not session_info:
            await safe_reply(update, "Session not found in database.")
            return
        await safe_reply(update, "🔄 Session found but inactive. Starting session...")
        thread_id = extract_thread_id(update)
        await start_agent_service(update, context, sid, session_info[1], thread_id)
        if sid not in acp_service.active_processes:
            await safe_reply(update, "Failed to start session.")
            return
    
    session = acp_service.active_processes[sid]
    if session.is_busy:
        await safe_reply(update, "Agent is busy.")
        return
    num_msgs = (
        int(context.args[0]) if context.args and context.args[0].isdigit() else 20
    )
    history = await db_service.get_recent_messages(sid, limit=num_msgs)
    if not history:
        await safe_reply(update, "No message history found.")
        return
    await safe_reply(
        update, f"🧠 *Injecting {len(history)} messages*...", parse_mode="Markdown"
    )
    context_text = "Here is the context of our previous conversation:\n\n" + "\n".join(
        [f"{r.upper()}: {c}" for r, c in history]
    )

    async def run_injection():
        session.is_busy = True
        try:
            async with typing_action(context, update.effective_chat.id):
                prompt_task = asyncio.create_task(
                    session.conn.prompt(
                        session_id=session.acp_session.session_id,
                        prompt=[text_block(context_text)],
                    )
                )

                thread_id = extract_thread_id(update)
                await send_safe_message(
                    context,
                    update.effective_chat.id,
                    "✅ *History injected.*",
                    parse_mode="Markdown",
                    message_thread_id=thread_id,
                )
                await prompt_task
        except Exception as e:
            thread_id = extract_thread_id(update)
            await send_safe_message(
                context,
                update.effective_chat.id,
                f"❌ Failed: {e}",
                message_thread_id=thread_id,
            )
        finally:
            if session.draft_streamer:
                await session.draft_streamer.close()
            session.is_busy = False

    asyncio.create_task(run_injection())


@register_command(
    "shutdown", "Shutdown the agent server", category="Session Management"
)
@authorized_only
async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = await get_current_session_id(update, context)
    if not sid:
        await safe_reply(update, "No active session selected to shutdown.")
        return

    session_info = await db_service.get_session(sid)
    if not session_info:
        await safe_reply(update, "Session data not found.")
        return

    name, path = session_info
    await safe_reply(
        update,
        f"🛑 Shutting down agent and background processes for `{name}`...",
        parse_mode="Markdown",
    )

    async with typing_action(context, update.effective_chat.id):
        # 1. Stop agent
        await acp_service.stop_session(sid)

        # 2. Stop all related background processes
        from telegram_acp_client.services.terminal_service import terminal_service

        killed_count = await terminal_service.kill_all_in_session(sid)

    await safe_reply(
        update,
        f"✅ Session `{name}` shutdown complete. (Agent stopped, {killed_count} processes killed)",
        parse_mode="Markdown",
    )


@register_command(
    "delete", "Delete an agent session", usage="[name]", category="Session Management"
)
@authorized_only
async def delete_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = extract_thread_id(update)
    chat_id = update.effective_chat.id

    if context.args:
        session_name = context.args[0]
        sid = await db_service.get_session_by_name(chat_id, thread_id, session_name)
        if not sid:
            await safe_reply(
                update,
                f"No session found with name `{session_name}`.",
                parse_mode="Markdown",
            )
            return
    else:
        sid = await get_current_session_id(update, context)
        if not sid:
            await safe_reply(
                update,
                "No active session to delete. You can specify a name: `/delete <name>`.",
                parse_mode="Markdown",
            )
            return

    session_info = await db_service.get_session(sid)
    if not session_info:
        await safe_reply(update, "Session data not found.")
        return

    name, path = session_info
    await safe_reply(
        update,
        f"🗑️ Deleting session `{name}` and exporting logs...",
        parse_mode="Markdown",
    )

    async with typing_action(context, update.effective_chat.id):
        # 1. Stop agent
        await acp_service.stop_session(sid)

        # 2. Stop all related background processes
        from telegram_acp_client.services.terminal_service import terminal_service

        await terminal_service.kill_all_in_session(sid)

        # 3. Export and delete from DB
        log_filepath = await db_service.export_and_delete_session(sid)

    if log_filepath and os.path.exists(log_filepath):
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=open(log_filepath, "rb"),
                caption=f"✅ Session `{name}` deleted. Logs exported.",
                message_thread_id=thread_id,
            )
        except Exception as e:
            await safe_reply(
                update, f"✅ Session deleted, but failed to send log file: {e}"
            )
    else:
        await safe_reply(update, f"✅ Session `{name}` deleted. (No logs exported)")

    # Clear active session from context
    clear_current_session_id(update, context)


@register_command(
    "detachsession", "Detach a session from its thread", category="Session Management"
)
@authorized_only
async def detach_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)

    linked_sessions = await db_service.get_linked_sessions(chat_id)

    if not linked_sessions:
        await safe_reply(update, "No sessions are currently linked to any thread.")
        return

    buttons = []
    for s_id, s_name, s_thread_id in linked_sessions:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📁 {s_name} (Thread #{s_thread_id})",
                    callback_data=("detach_session", s_id),
                )
            ]
        )

    await send_safe_message(
        context,
        chat_id,
        "Select a session to detach from its thread:",
        reply_markup=InlineKeyboardMarkup(buttons),
        message_thread_id=thread_id,
    )

@router.register("switch")
async def on_switch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid):
    query = update.callback_query
    thread_id = extract_thread_id(update)
    if thread_id:
        await safe_answer(
            query,
            "Session switching is disabled inside topics.",
            show_alert=True,
        )
        return
    await set_current_session_id(update, context, sid)
    if sid not in acp_service.active_processes:
        session_info = await db_service.get_session(sid)
        if session_info:
            await start_agent_service(
                update, context, sid, session_info[1], thread_id
            )
    await safe_edit(query, f"Switched to Session ID: {sid}")

@router.register("detach_session")
async def on_detach_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, sid):
    query = update.callback_query
    session_info = await db_service.get_session(int(sid))
    if session_info:
        await db_service.detach_session_from_thread(int(sid))
        await safe_edit(
            query, f"🔓 Session `{session_info[0]}` detached from its thread."
        )
    else:
        await safe_answer(query, "Session not found.", show_alert=True)

