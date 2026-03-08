import asyncio
import logging
import sys
import argparse
import os
import json
import subprocess
from pathlib import Path
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

from telegram_acp_client.config import settings


def get_default_config_root():
    xdg_config = os.getenv("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "telegram-acp-client"
    return Path.home() / ".config" / "telegram-acp-client"


async def post_init(application):
    from telegram_acp_client.services.db_service import db_service

    logging.info(f"Initializing database at: {settings.DATABASE_PATH}")
    await db_service.init_db()


async def error_handler(update, context):
    logging.error(f"Update {update} caused error {context.error}")


def run_bot(config_dir: str):
    # 1. Load Settings
    settings.load(config_dir)

    # 2. Setup Logging
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=log_level,
    )
    logging.getLogger().setLevel(log_level)
    logging.info(f"🤖 Bot starting from: {config_dir}")

    if not settings.TELEGRAM_BOT_TOKEN:
        logging.error("No TELEGRAM_TOKEN found in config!")
        sys.exit(1)

    # 3. Deferred imports to ensure settings are loaded first
    from telegram_acp_client.bot.common import start_command, help_command
    from telegram_acp_client.bot.session import (
        new_session_command,
        list_sessions_command,
        restart_command,
        stop_command,
        history_inject_command,
    )
    from telegram_acp_client.bot.navigation import ls_command, cd_command
    from telegram_acp_client.bot.process import (
        shell_command,
        ps_command,
        kill_command,
        logs_command,
    )
    from telegram_acp_client.bot.agent import handle_message, handle_callback

    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .arbitrary_callback_data(True)
        .build()
    )
    app.add_error_handler(error_handler)

    handlers = [
        ("start", start_command),
        ("help", help_command),
        ("new", new_session_command),
        ("sessions", list_sessions_command),
        ("restart", restart_command),
        ("stop", stop_command),
        ("historyInject", history_inject_command),
        ("ls", ls_command),
        ("cd", cd_command),
        ("shell", shell_command),
        ("ps", ps_command),
        ("kill", kill_command),
        ("logs", logs_command),
    ]
    for cmd, handler in handlers:
        app.add_handler(CommandHandler(cmd, handler))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


def cmd_new(args):
    bot_dir = get_default_config_root() / args.name
    bot_dir.mkdir(parents=True, exist_ok=True)
    config_file = bot_dir / "bot.json"

    if config_file.exists() and not args.force:
        print(
            f"Error: Config already exists at {config_file}. Use --force to overwrite."
        )
        return

    token = input("Enter Telegram Bot Token: ").strip()
    users = input("Enter Allowed Usernames (comma separated): ").strip()
    agent_cmd = (
        input("Enter Agent Command [gemini-cli --experimental-acp]: ").strip()
        or "gemini-cli --experimental-acp"
    )

    config = {
        "telegram_token": token,
        "allowed_users": [u.strip() for u in users.split(",") if u.strip()],
        "agent_command": agent_cmd,
        "log_level": "INFO",
    }

    config_file.write_text(json.dumps(config, indent=4))
    print(f"\n✅ Created config at {config_file}")

    if args.no_start:
        print(
            f"\n📝 Service start skipped. To start manually: telegram-acp-client start {args.name}"
        )
        return

    print(f"\n🚀 Setting up systemd user service for '{args.name}'...")
    try:
        # 1. Reload daemon
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        # 2. Enable service
        subprocess.run(
            [
                "systemctl",
                "--user",
                "enable",
                f"telegram-acp-client@{args.name}.service",
            ],
            check=True,
        )
        # 3. Start service
        subprocess.run(
            [
                "systemctl",
                "--user",
                "start",
                f"telegram-acp-client@{args.name}.service",
            ],
            check=True,
        )
        print(f"✅ Service 'telegram-acp-client@{args.name}' is now running.")
    except Exception as e:
        print(f"⚠️ Warning: Could not automatically start service: {e}")
        print(
            f"Please ensure the template 'telegram-acp-client@.service' is installed in ~/.config/systemd/user/ or /usr/lib/systemd/user/"
        )


def cmd_run(args):
    run_bot(args.config)


def manage_service(bot_name, action):
    service_name = f"telegram-acp-client@{bot_name}.service"
    cmd = ["systemctl", "--user", action, service_name]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(prog="telegram-acp-client")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run
    run_parser = subparsers.add_parser("run", help="Run a bot instance")
    run_parser.add_argument("--config", help="Path to bot config directory")

    # new
    new_parser = subparsers.add_parser("new", help="Setup a new bot configuration")
    new_parser.add_argument("name", help="Unique name for the bot")
    new_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing config"
    )
    new_parser.add_argument(
        "--no-start", action="store_true", help="Do not automatically start the service"
    )

    # status / restart / stop / start
    for cmd in ["status", "restart", "start", "stop", "enable", "disable"]:
        p = subparsers.add_parser(cmd, help=f"{cmd.capitalize()} the bot service")
        p.add_argument("name", help="Name of the bot")

    # logs
    log_p = subparsers.add_parser("logs", help="View bot service logs")
    log_p.add_argument("name", help="Name of the bot")
    log_p.add_argument("-f", "--follow", action="store_true", help="Follow logs")

    args = parser.parse_args()

    if args.command == "new":
        cmd_new(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command in ["status", "restart", "stop", "enable", "disable"]:
        manage_service(args.name, args.command)
    elif args.command == "logs":
        cmd = ["journalctl", "--user", "-u", f"telegram-acp-client@{args.name}.service"]
        if args.follow:
            cmd.append("-f")
        subprocess.run(cmd)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
