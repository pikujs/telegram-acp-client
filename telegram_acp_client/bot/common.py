from telegram import Update
from telegram.ext import ContextTypes
from telegram_acp_client.bot.utils import authorized_only

HELP_TEXT = """👋 *Welcome to Telegram ACP Client!*

*Session Management:*
/new <name> <path> - Create Workspace
/sessions - List & Switch Sessions
/restart - Restart current agent
/stop - Stop current agent task
/shutdown - Shut down agent and all processes
/historyInject <n> - Inject last n messages

*Local Navigation:*
/ls - List files in current directory
/cd <dir> - Change directory
/shell <cmd> - Run background process
/ps - List running processes
/logs <id> - View process logs
/kill <id> - Stop a process
/help - Show this message

Send any other message to talk to the AI Agent!"""

@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
