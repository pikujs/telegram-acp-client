from telegram import Update
from telegram.ext import ContextTypes
from telegram_acp_client.bot.utils import authorized_only, typing_action
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.terminal_service import terminal_service

@authorized_only
async def shell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /shell <command>")
        return

    sid = context.user_data.get("current_session_id")
    if not sid:
        await update.message.reply_text("Select a session first via /sessions.")
        return

    session_info = await db_service.get_session(sid)
    cwd = terminal_service.get_cwd(update.effective_chat.id, session_info[1])
    cmd = " ".join(context.args)

    chat_id = update.effective_chat.id

    async def send_log(log_msg):
        await context.bot.send_message(
            chat_id=chat_id, text=log_msg, parse_mode="Markdown"
        )

    async with typing_action(context, chat_id):
        task_id = await terminal_service.run_shell(chat_id, cmd, cwd, send_log, session_id=sid)
        await update.message.reply_text(
            f"⚙️ Started background task: `{task_id}`", parse_mode="Markdown"
        )

@authorized_only
async def ps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = terminal_service.get_active_tasks()
    if not tasks:
        await update.message.reply_text("No active background processes.")
        return
    
    msg = "⚙️ *Active Background Processes*:\n\n"
    for t in tasks:
        msg += f"- `{t.task_id}`: `{t.command}`\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

@authorized_only
async def kill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /kill <task_id>")
        return
    
    task_id = context.args[0]
    success = await terminal_service.kill_task(task_id)
    if success:
        await update.message.reply_text(f"🛑 Killed process: `{task_id}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ Could not kill process: `{task_id}`. It may already be finished.")

@authorized_only
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /logs <task_id> [num_lines]")
        return
    
    task_id = context.args[0]
    num_lines = 50
    if len(context.args) > 1:
        try:
            num_lines = int(context.args[1])
        except ValueError:
            pass
            
    logs = terminal_service.get_logs(task_id, num_lines)
    if logs is None:
        await update.message.reply_text(f"❌ Process `{task_id}` not found.")
    elif not logs:
        await update.message.reply_text(f"📋 No logs found for `{task_id}`.")
    else:
        log_text = "\n".join(logs)
        # Handle message length limit
        if len(log_text) > 4000:
            log_text = "..." + log_text[-4000:]
        await update.message.reply_text(f"📋 *Logs for `{task_id}`* (last {len(logs)} lines):\n```\n{log_text}\n```", parse_mode='Markdown')
