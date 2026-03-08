import os
import json
import logging
from dataclasses import dataclass, field
from typing import List
from pathlib import Path
from dotenv import load_dotenv


@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = ""
    ALLOWED_USERS: List[str] = field(default_factory=list)
    AGENT_COMMAND: str = "gemini-cli"
    LOG_LEVEL: str = "INFO"
    CONFIG_DIR: Path = Path.cwd()
    DATABASE_PATH: str = "database.db"

    def load(self, config_dir: str = None):
        # 1. Determine config directory
        env_config_dir = os.getenv("TELEGRAM_ACP_CONFIG_DIR")
        target_dir = config_dir or env_config_dir

        print(f"targetDir: {target_dir}")

        if target_dir:
            self.CONFIG_DIR = Path(target_dir).expanduser().resolve()
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self.DATABASE_PATH = str(self.CONFIG_DIR / "database.db")

            json_config = self.CONFIG_DIR / "bot.json"
            if json_config.exists():
                try:
                    data = json.loads(json_config.read_text())
                    self.TELEGRAM_BOT_TOKEN = data.get("telegram_token", "")
                    self.ALLOWED_USERS = data.get("allowed_users", [])
                    self.AGENT_COMMAND = data.get("agent_command", "gemini-cli")
                    self.LOG_LEVEL = data.get("log_level", "INFO")
                except Exception as e:
                    print(f"Error loading JSON config: {e}")
        else:
            # Fallback to .env for local development
            load_dotenv()
            self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
            users = os.getenv("ALLOWED_USERS", "")
            self.ALLOWED_USERS = [u.strip() for u in users.split(",") if u.strip()]
            self.AGENT_COMMAND = os.getenv("AGENT_COMMAND", "gemini-cli")
            self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
            self.DATABASE_PATH = "database.db"


settings = Settings()
