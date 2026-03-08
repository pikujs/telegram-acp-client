import os
from telegram import Update
from telegram.ext import ContextTypes
from telegram_acp_client.bot.utils import authorized_only, typing_action, escape_markdown
from telegram_acp_client.services.db_service import db_service
from telegram_acp_client.services.terminal_service import terminal_service

@authorized_only
async def ls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_session_id")
    if not sid:
        await update.message.reply_text("Select a session first via /sessions.")
        return

    session_info = await db_service.get_session(sid)
    cwd = terminal_service.get_cwd(update.effective_chat.id, session_info[1])

    async with typing_action(context, update.effective_chat.id):
        try:
            files = os.listdir(cwd)
            files_list = "\n".join([f"📄 {escape_markdown(f)}" for f in files if not f.startswith(".")])
            await update.message.reply_text(
                f"📁 *Directory Listing ({escape_markdown(cwd)})*:\n{files_list or '_Empty_'}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

@authorized_only
async def cd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /cd <directory>")
        return

    sid = context.user_data.get("current_session_id")
    if not sid:
        await update.message.reply_text("Select a session first.")
        return

    session_info = await db_service.get_session(sid)
    cwd = terminal_service.get_cwd(update.effective_chat.id, session_info[1])

    target = context.args[0]
    new_path = os.path.abspath(os.path.join(cwd, target))

    if os.path.isdir(new_path):
        terminal_service.set_cwd(update.effective_chat.id, new_path)
        await update.message.reply_text(
            f"📍 *Changed directory to:* `{escape_markdown(new_path)}`", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ Directory not found: `{new_path}`")

@authorized_only
async def cat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /cat <filename>")
        return

    sid = context.user_data.get("current_session_id")
    if not sid:
        await update.message.reply_text("Select a session first.")
        return

    session_info = await db_service.get_session(sid)
    cwd = terminal_service.get_cwd(update.effective_chat.id, session_info[1])

    target = context.args[0]
    file_path = os.path.abspath(os.path.join(cwd, target))

    if os.path.isfile(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if len(content) > 3500:
                content = content[:3500] + "\n... (truncated)"

            ext = os.path.splitext(file_path)[1][1:] or "text"

            await update.message.reply_text(
                f"📄 *{escape_markdown(target)}*:\n```{ext}\n{content}\n```", parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error reading file: {str(e)}")
    else:
        await update.message.reply_text(f"❌ File not found: `{file_path}`")
