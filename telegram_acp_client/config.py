import os
import json
import logging
from dataclasses import dataclass, field
from typing import List
from pathlib import Path
from platformdirs import user_config_dir, user_data_dir


@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = ""
    ALLOWED_USERS: List[str] = field(default_factory=list)
    AGENT_COMMAND: str = "gemini-cli --experimental-acp"
    LOG_LEVEL: str = "INFO"
    CONFIG_DIR: Path = Path.cwd()
    DATA_DIR: Path = Path.cwd()
    DATABASE_PATH: str = "database.db"

    def load(self, config_file: str = None, bot_name: str = None):
        # 1. Determine base directories
        default_config_root = Path(user_config_dir("telegram-acp-client"))
        default_data_root = Path(user_data_dir("telegram-acp-client"))

        # 2. Determine config file path and data directory
        if config_file:
            target_config_file = Path(config_file).expanduser().resolve()
            # Derive bot name from filename if not explicitly provided
            self.bot_name = bot_name or target_config_file.stem

            if bot_name is None:
                # If config file is provided but bot_name was not explicitly passed,
                # store the database locally next to the config file.
                self.DATA_DIR = target_config_file.parent
            else:
                self.DATA_DIR = default_data_root
        else:
            self.bot_name = bot_name or "default"
            # Look for {bot_name}.json in the default config root
            target_config_file = default_config_root / f"{self.bot_name}.json"
            self.DATA_DIR = default_data_root

        self.CONFIG_DIR = target_config_file.parent
        self.DATABASE_PATH = str(self.DATA_DIR / f"{self.bot_name}.db")

        # Ensure directories exist
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)

        if target_config_file.exists():
            try:
                data = json.loads(target_config_file.read_text())
                self.TELEGRAM_BOT_TOKEN = data.get("telegram_token", "")
                self.ALLOWED_USERS = data.get("allowed_users", [])
                self.AGENT_COMMAND = data.get("agent_command", "gemini-cli")
                self.LOG_LEVEL = data.get("log_level", "INFO")
            except Exception as e:
                print(f"Error loading JSON config: {e}")
        else:
            print(f"⚠️ Warning: Config file not found at {target_config_file}")


settings = Settings()
