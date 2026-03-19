from typing import Callable, NamedTuple, Dict, List

from telegram import BotCommand


class CommandInfo(NamedTuple):
    name: str
    description: str
    handler: Callable
    usage: str = ""
    category: str = "General"


COMMANDS: Dict[str, CommandInfo] = {}


def register_command(name: str, description: str, usage: str = "", category: str = "General"):
    """
    Decorator to register a telegram bot command.
    """
    def decorator(func: Callable):
        COMMANDS[name] = CommandInfo(name, description, func, usage, category)
        return func
    return decorator


def get_bot_commands() -> List[BotCommand]:
    """
    Returns the list of commands formatted for Telegram's set_my_commands.
    """
    return [BotCommand(cmd.name, cmd.description) for cmd in COMMANDS.values()]


def generate_help_text() -> str:
    """
    Generates the formatted help text from registered commands.
    """
    text = "👋 *Welcome to Telegram ACP Client!*\n\n"
    
    # Group by category
    categories: Dict[str, List[CommandInfo]] = {}
    for cmd in COMMANDS.values():
        categories.setdefault(cmd.category, []).append(cmd)
        
    for category, cmds in categories.items():
        text += f"*{category}:*\n"
        for cmd in cmds:
            usage_str = f" {cmd.usage}" if cmd.usage else ""
            text += f"/{cmd.name}{usage_str} - {cmd.description}\n"
        text += "\n"
        
    text += "Send any other message to talk to the AI Agent!"
    return text
