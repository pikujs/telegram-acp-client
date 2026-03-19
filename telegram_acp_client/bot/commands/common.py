from telegram import Update
from telegram.ext import ContextTypes

from telegram_acp_client.bot.auth import authorized_only
from telegram_acp_client.bot.messaging import safe_reply
from telegram_acp_client.bot.registry import generate_help_text, register_command

@register_command("start", "Start the bot and see main menu", category="General")
@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, generate_help_text(), parse_mode="Markdown")

@register_command("help", "Show help information", category="General")
@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, generate_help_text(), parse_mode="Markdown")
