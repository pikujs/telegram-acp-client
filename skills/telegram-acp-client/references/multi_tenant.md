# Multi-Tenant Management

The `telegram-acp-client` is designed to handle multiple independent bot instances on the same machine.

## How it Works
Each instance is identified by a unique `name`. This name is used to:
1.  Locate the config file: `<name>.json`
2.  Locate the database file: `<name>.db`
3.  Name the background service: `telegram-acp-client@<name>`

## Managing Instances

### Creating a New Instance
To add a new bot (e.g., `work-bot`):
1.  Obtain a new Telegram Token.
2.  Run `uv run telegram-acp-client new work-bot`.
3.  Follow the prompts.

### Listing Instances
To see all configured bots, list the files in the config directory:
```bash
ls ~/.config/telegram-acp-client/*.json
```

### Isolated Data
Each bot's data (database, session history) is isolated:
- `default` -> `default.db`
- `work-bot` -> `work-bot.db`

### Simultaneous Running
Multiple bots can run simultaneously as separate background services. They will each respond to their own Telegram Token.
```bash
uv run telegram-acp-client start bot1
uv run telegram-acp-client start bot2
```
