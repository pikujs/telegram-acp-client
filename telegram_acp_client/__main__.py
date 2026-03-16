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
from platformdirs import user_config_dir, user_data_dir

from telegram_acp_client.config import settings
from telegram_acp_client.services.service_manager import get_service_manager


def get_default_config_root():
    # Use platform-specific user config dir
    return Path(user_config_dir("telegram-acp-client"))


def get_default_data_root():
    # Use platform-specific user data dir
    return Path(user_data_dir("telegram-acp-client"))


async def post_init(application):
    from telegram_acp_client.services.db_service import db_service

    logging.info(f"Initializing database at: {settings.DATABASE_PATH}")
    await db_service.init_db()


async def error_handler(update, context):
    logging.error(f"Update {update} caused error {context.error}")


def run_bot(config_file: str = None, bot_name: str = None):
    # 1. Load Settings
    settings.load(config_file=config_file, bot_name=bot_name)

    # 2. Setup Logging
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    log_file = settings.DATA_DIR / f"{settings.bot_name}.log"
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=log_level,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding='utf-8')
        ]
    )
    logging.getLogger().setLevel(log_level)
    
    source = bot_name or config_file or "default"
    logging.info(f"🤖 Bot starting (Instance: {source})")
    logging.info(f"📂 Config root: {settings.CONFIG_DIR}")
    logging.info(f"🗄️ Database: {settings.DATABASE_PATH}")

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
        shutdown_command,
        delete_session_command,
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
        ("shutdown", shutdown_command),
        ("delete", delete_session_command),
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
    name = args.name or "default"
    config_root = get_default_config_root()
    data_root = get_default_data_root()

    config_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    
    config_file = config_root / f"{name}.json"
    data_file = data_root / f"{name}.db"

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
    print(f"✅ Data will be stored at {data_file}")

    try:
        from telegram_acp_client.services.os_service_manager import get_manager
        manager = get_manager(name)
        print(f"\n🚀 Setting up background service for '{name}'...")
        manager.install()

        if args.no_start:
            print(f"\n📝 Service start skipped. To start manually: telegram-acp-client start {name}")
            return

        manager.enable()
        manager.start()
        print(f"✅ Service '{name}' is now running.")
    except Exception as e:
        print(f"⚠️ Warning: Could not automatically start service: {e}")


def cmd_run(args):
    if args.config:
        run_bot(config_file=args.config)
    else:
        run_bot(bot_name=args.name)


def manage_service(bot_name, action):
    name = bot_name or "default"
    try:
        from telegram_acp_client.services.os_service_manager import get_manager
        manager = get_manager(name)
        func = getattr(manager, action)
        func()
    except Exception as e:
        print(f"❌ Error: {e}")


def main():
    parser = argparse.ArgumentParser(prog="telegram-acp-client")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run
    run_parser = subparsers.add_parser("run", help="Run a bot instance")
    run_parser.add_argument("name", nargs="?", help="Name of the bot (default: default)")
    run_parser.add_argument("--config", help="Path to bot config JSON file (overrides name)")

    # new
    new_parser = subparsers.add_parser("new", help="Setup a new bot configuration")
    new_parser.add_argument("name", nargs="?", help="Unique name for the bot (default: default)")
    new_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing config"
    )
    new_parser.add_argument(
        "--no-start", action="store_true", help="Do not automatically start the service"
    )

    # status / restart / stop / start
    for cmd in ["status", "restart", "start", "stop", "enable", "disable"]:
        p = subparsers.add_parser(cmd, help=f"{cmd.capitalize()} the bot service")
        p.add_argument("name", nargs="?", help="Name of the bot (default: default)")

    # logs
    log_p = subparsers.add_parser("logs", help="View bot service logs")
    log_p.add_argument("name", nargs="?", help="Name of the bot (default: default)")
    log_p.add_argument("-f", "--follow", action="store_true", help="Follow logs")

    args = parser.parse_args()

    if args.command == "new":
        cmd_new(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command in ["status", "restart", "stop", "enable", "disable"]:
        manage_service(args.name, args.command)
    elif args.command == "logs":
        name = args.name or "default"
        try:
            from telegram_acp_client.services.os_service_manager import get_manager
            manager = get_manager(name)
            manager.logs(follow=args.follow)
        except Exception as e:
            print(f"❌ Error: {e}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
