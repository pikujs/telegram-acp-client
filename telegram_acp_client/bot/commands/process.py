from telegram import Update
from telegram.ext import ContextTypes

from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.messaging import (
    safe_reply,
    send_safe_message,
    typing_action,
)
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.terminal_service import terminal_service
from telegram_acp_client.bot.registry import register_command
from telegram_acp_client.bot.threads import get_current_session_id, extract_thread_id


@register_command("shell", "Run a shell command", usage="<command>", category="Local Navigation")
@authorized_only
async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Usage: /shell <command>")
        return

    sid = await get_current_session_id(update, context)
    if not sid:
        await safe_reply(update, "Select a session first via /sessions.")
        return

    session_info = await db_service.get_session(sid)
    cwd = terminal_service.get_cwd(update.effective_chat.id, session_info[1])
    cmd = " ".join(context.args)

    chat_id = update.effective_chat.id
    thread_id = extract_thread_id(update)

    async def send_log(log_msg):
        await send_safe_message(context, chat_id, log_msg, parse_mode="Markdown", message_thread_id=thread_id)

    async with typing_action(context, chat_id):
        task_id = await terminal_service.run_shell(chat_id, cmd, cwd, send_log, session_id=sid)
        await safe_reply(update,
            f"⚙️ Started background task: `{task_id}`", parse_mode="Markdown"
        )

@register_command("ps", "List background processes", category="Local Navigation")
@authorized_only
async def ps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = terminal_service.get_active_tasks()
    if not tasks:
        await safe_reply(update, "No active background processes.")
        return

    msg = "⚙️ *Active Background Processes*:\n\n"
    for t in tasks:
        msg += f"- `{t.task_id}`: `{t.command}`\n"
    await safe_reply(update, msg, parse_mode='Markdown')

@register_command("kill", "Kill a background process", usage="<task_id>", category="Local Navigation")
@authorized_only
async def kill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Usage: /kill <task_id>")
        return

    task_id = context.args[0]
    success = await terminal_service.kill_task(task_id)
    if success:
        await safe_reply(update, f"🛑 Killed process: `{task_id}`", parse_mode='Markdown')
    else:
        await safe_reply(update, f"❌ Could not kill process: `{task_id}`. It may already be finished.")

@register_command("logs", "View logs of a process", usage="<task_id> [num_lines]", category="Local Navigation")
@authorized_only
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Usage: /logs <task_id> [num_lines]")
        return

    task_id = context.args[0]
    num_lines = 7
    if len(context.args) > 1:
        try:
            num_lines = int(context.args[1])
        except ValueError:
            pass

    logs = terminal_service.get_logs(task_id, num_lines)
    if logs is None:
        await safe_reply(update, f"❌ Process `{task_id}` not found.")
    elif not logs:
        await safe_reply(update, f"📋 No logs found for `{task_id}`.")
    else:
        log_text = "\n".join(logs)
        # Handle message length limit
        if len(log_text) > 4000:
            log_text = "..." + log_text[-4000:]
        await safe_reply(update, f"📋 *Logs for `{task_id}`* (last {len(logs)} lines):\n```\n{log_text}\n```", parse_mode='Markdown')
