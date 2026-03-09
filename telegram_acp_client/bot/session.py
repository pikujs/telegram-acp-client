import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.messaging import typing_action, safe_reply, send_safe_message
from telegram_acp_client.bot.agent import start_agent_service
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.acp_service import acp_service
from acp import text_block

@authorized_only
async def new_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await safe_reply(update, "Usage: /new <name> <absolute_path>")
        return
    name, path = context.args[0], os.path.abspath(context.args[1])
    os.makedirs(path, exist_ok=True)
    async with typing_action(context, update.effective_chat.id):
        db_id = await db_service.create_session(update.effective_chat.id, name, path)
        await db_service.set_last_session(update.effective_chat.id, db_id)
        await start_agent_service(update, context, db_id, path)

@authorized_only
async def list_sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions = await db_service.get_sessions(update.effective_chat.id)
    if not sessions:
        await safe_reply(update, "No sessions. Use /new.")
        return
    keyboard = []
    for sid, name, path in sessions:
        status = "🟢" if sid in acp_service.active_processes else "⚪"
        keyboard.append([InlineKeyboardButton(f"{status} {name}", callback_data=("switch", sid))])
    await safe_reply(update, "Workspaces:", reply_markup=InlineKeyboardMarkup(keyboard))

@authorized_only
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_session_id") or await db_service.get_last_session_id(update.effective_chat.id)
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
        await start_agent_service(update, context, sid, path)
        if sid in acp_service.active_processes:
            acp_service.active_processes[sid].is_busy = False
    await safe_reply(update, f"✅ Agent for `{name}` has been restarted.")

@authorized_only
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_session_id") or await db_service.get_last_session_id(update.effective_chat.id)
    if not sid or sid not in acp_service.active_processes:
        await safe_reply(update, "No active agent session to stop.")
        return
    session = acp_service.active_processes[sid]
    if not session.is_busy:
        await safe_reply(update, "Agent is not currently performing any task.")
        return
    try:
        await session.conn.cancel(session_id=session.acp_session.session_id)
        await safe_reply(update, "⏹️ *Stop signal sent*.", parse_mode='Markdown')
    except Exception as e:
        await safe_reply(update, f"❌ Failed to stop task: {e}")

@authorized_only
async def history_inject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_session_id") or await db_service.get_last_session_id(update.effective_chat.id)
    if not sid or sid not in acp_service.active_processes:
        await safe_reply(update, "No active agent session.")
        return
    session = acp_service.active_processes[sid]
    if session.is_busy:
        await safe_reply(update, "Agent is busy.")
        return
    num_msgs = int(context.args[0]) if context.args and context.args[0].isdigit() else 20
    history = await db_service.get_recent_messages(sid, limit=num_msgs)
    if not history:
        await safe_reply(update, "No message history found.")
        return
    await safe_reply(update, f"🧠 *Injecting {len(history)} messages*...", parse_mode='Markdown')
    context_text = "Here is the context of our previous conversation:\n\n" + "\n".join([f"{r.upper()}: {c}" for r, c in history])
    async def run_injection():
        session.is_busy = True
        try:
            async with typing_action(context, update.effective_chat.id):
                prompt_task = asyncio.create_task(session.conn.prompt(session_id=session.acp_session.session_id, prompt=[text_block(context_text)]))
                await send_safe_message(context, update.effective_chat.id, "✅ *History injected.*", parse_mode='Markdown')
                await prompt_task
        except Exception as e:
            await send_safe_message(context, update.effective_chat.id, f"❌ Failed: {e}")
        finally:
            if session.streamer:
                await session.streamer.close()
                session.streamer = None
            session.is_busy = False
    asyncio.create_task(run_injection())

@authorized_only
async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_session_id") or await db_service.get_last_session_id(update.effective_chat.id)
    if not sid:
        await safe_reply(update, "No active session selected to shutdown.")
        return
        
    session_info = await db_service.get_session(sid)
    if not session_info:
        await safe_reply(update, "Session data not found.")
        return
        
    name, path = session_info
    await safe_reply(update, f"🛑 Shutting down agent and background processes for `{name}`...", parse_mode='Markdown')
    
    async with typing_action(context, update.effective_chat.id):
        # 1. Stop agent
        await acp_service.stop_session(sid)
        
        # 2. Stop all related background processes
        from telegram_acp_client.services.terminal_service import terminal_service
        killed_count = await terminal_service.kill_all_in_session(sid)
        
    await safe_reply(update, f"✅ Session `{name}` shutdown complete. (Agent stopped, {killed_count} processes killed)", parse_mode='Markdown')
