# AGENTS.md - Telegram ACP Client Development Guide

## 🏗 Architecture Overview

The application is built using an asynchronous, service-oriented architecture designed to handle long-running agent interactions and background shell tasks without blocking the Telegram update loop. Since v0.2.0, it supports **Multi-Tenant CLI management**, allowing multiple bot instances to run independently.

For detailed information on the communication protocol used between the bot and the agent, see [ACP Protocol Reference](docs/acp_protocol.md).

### Core Components
- **CLI Wrapper (`__main__.py`):** Handles bot lifecycle, service orchestration, and instance creation.
- **ACP Service (`services/acp_service.py`):** Manages the lifecycle of the agent subprocess. Uses `stdio` for communication and handles the Agent Client Protocol handshakes and tool requests.
- **Terminal Service (`services/terminal_service.py`):** Handles background shell execution. Maintains a log buffer for each task and manages per-chat CWD (Current Working Directory).
- **DB Service (`services/db_service.py`):** Uses `aiosqlite` for persistent storage. Database paths are dynamic based on the active bot instance.
- **Bot Modules (`bot/`):** Segregated command handlers and utility functions.
    - `agent.py`: Handles the core conversation and permission delegation.
    - `session.py`: Manages workspace lifecycles.
    - `process.py`: Handles system-level background jobs.
    - `auth.py`, `formatting.py`, `messaging.py`: Modular helpers for security, pure formatting, and resilient API wrappers.

## 📜 CLI Commands Reference

### `new <name>`
- Creates the directory structure in platform-specific user config dir.
- Generates `{name}.json` with your credentials.
- Prints a template for a systemd unit file.

### `run [--config PATH] [name]`
- The actual bot execution engine.
- If `name` is provided, it looks for `{name}.json` in the default config root.
- This is the entry point used by the systemd service.

### `status/restart/stop/enable/disable/start <name>`
- Cross-platform wrappers around the native OS service manager (`systemctl` on Linux, `launchctl` on macOS, `schtasks` on Windows).

### `logs <name> [-f]`
- Tails the bot's log file automatically using native commands (`journalctl`, `tail`, or `powershell` depending on the platform).

## 📜 Development Rules & Mandates

### 1. Concurrency & Deadlocks
- **Mandate:** NEVER await a blocking agent operation (like `conn.prompt`) directly inside a Telegram handler. 
- **Reason:** Telegram chat updates are processed sequentially. If you await a prompt that eventually requests a permission button click, you will deadlock the bot.
- **Solution:** Always wrap agent prompts in `asyncio.create_task()` within the handler.

### 2. Authorization
- **Mandate:** All handlers that interact with the system or agent must be protected.
- **Standard:** Use the `@authorized_only` decorator from `bot.auth`.

### 3. Tool Permissions
- **Mandate:** Permission prompts must be informative and persistent.
- **Details:** 
    - Use `send_split_diff` for large file edits.
    - Use `is_approval_option` for centralizing keyword detection (`allow`, `accept`, etc.).
    - When updating a permission message (e.g., to "Granted"), preserve the original tool call description for the chat history.

### 4. Logging
- **Mandate:** Log all outgoing messages and tool lifecycle events.
- **Standard:** Every outgoing message should pass through `send_safe_message` or `send_split_diff` which includes logging. Log tool starts, permission results, and file I/O.

### 5. Markdown Formatting
- **Mandate:** Respect Telegram's Markdown V1 limitations.
- **Rule:** Use `escape_markdown` for any variable text. It now handles a wider range of special characters (`_`, `*`, `` ` ``, `[`, `]`, `(`, `)`) to prevent parsing errors.
- **Resilience:** The messaging system automatically falls back to plain text if Markdown parsing fails despite escaping.

### 6. Multi-Modal Support
- **Capability:** The bot supports sending images (photos) and voice messages to the agent.
- **Handling:** Media files are automatically downloaded, base64-encoded, and wrapped in ACP `image_block` or `audio_block` for the `session/prompt` call.
- **Storage:** Multi-modal messages are saved in the database with descriptive placeholders (e.g., `[Image]`, `[Voice]`).

### 7. Security Checks
- **Mandate:** Before committing any changes, the developer (or agent) must perform a manual audit of the staged diffs and obtain user confirmation.
- **Workflow:**
    1. **Stage:** `git add <files>`
    2. **Audit:** `git diff --staged` (Look for tokens, keys, local paths, or regressions).
    3. **Confirm:** Ask the user for confirmation to proceed with the commit and push.
    4. **Commit/Push:** Execute only after user approval.
- **Checklist:**
    - No Telegram Bot Tokens (e.g., `123456:ABC...`).
    - No personal user names or absolute local paths (e.g., `/home/username/...`).
    - No hardcoded credentials or environment-specific values in the code.
    - Ensure `.gitignore` correctly covers any new sensitive files (like `.db` or `.json` config files).
- **Tool:** Use `git diff --staged` and search for common patterns like `TOKEN`, `KEY`, or `/home/`.

### 7. Linting & Formatting
- **Mandate:** There is no need to actively run `ruff check` or `ruff format` during automated agent workflows unless explicitly requested by the user. Rely on the IDE or pre-commit hooks (if set up) for general linting, or simply let the user run it later. This saves context and time.

### 8. Python Environment (UV Usage)
- **Mandate:** ALWAYS use `uv run` for executing python commands or scripts within this repository. Do not use plain `python` or `python3` commands.

## 🔄 Core Workflows

### Creating a New Command
1. Add the handler function to the appropriate module in `bot/`.
2. Annotate with `@authorized_only`.
3. If it's long-running, wrap the logic in `async with typing_action(context, chat_id):`.
4. Register the command in `__main__.py`.
5. Update `HELP_TEXT` in `bot/common.py`.

### Agent Tool Approval Flow
1. Agent requests permission -> `acp_service.request_permission` is called.
2. Bot creates a `PermissionNode` and registers it in `session.permission_nodes`.
3. `PermissionNode.render()` sends buttons to the user (via `on_permission`).
4. User clicks button -> `on_perm_callback` is triggered.
5. `PermissionNode.handle_click()` processes the choice, sets the future, and updates the message.
6. The `acp_service` receives the result from the awaited future and returns it to the agent.

## 🛠 Local Development Setup

### 1. Development Systemd Service (`telegram-acp-dev@.service`)
This repository includes a systemd template file (`telegram-acp-dev@.service`) designed specifically for local development.

**How it Works:**
Instead of relying on a globally installed binary, this service uses `uv run` to execute the CLI directly from the cloned repository's source code. This means any modifications you make to the Python files are immediately active the next time you restart the service, without needing to reinstall the package. The service is configured to look for a configuration file named `{botname}.json` in the repository's root directory.

**Setup:**
1. Symlink the service file to your systemd user directory:
   ```bash
   mkdir -p ~/.config/systemd/user/
   ln -sf $(pwd)/telegram-acp-dev@.service ~/.config/systemd/user/
   ```
2. Reload systemd:
   ```bash
   systemctl --user daemon-reload
   ```

**Usage & Workflows:**

#### Managing the Dev Service:
- **Start:** `systemctl --user start telegram-acp-dev@<botname>`
- **Stop:** `systemctl --user stop telegram-acp-dev@<botname>`
- **Restart:** `systemctl --user restart telegram-acp-dev@<botname>` (Run this after saving code changes!)
- **Logs:** `journalctl --user -u telegram-acp-dev@<botname> -f`

#### Workflow: Adding a New Dev Bot (Agent / Automated Workflow)
Since interactive commands (like `uv run telegram-acp-client new`) hang when run by an agent, the correct way for an agent to provision a new bot for local development is to create the configuration file manually:

1. **Create the config file** directly in the repository root as `<botname>.json` (e.g., `my-dev-bot.json`):
   ```json
   {
       "telegram_token": "YOUR_BOT_TOKEN",
       "allowed_user_ids": [12345678],
       "agent_command": "gemini --experimental-acp",
       "log_level": "INFO"
   }
   ```
2. **Start the dev service**:
   ```bash
   systemctl --user start telegram-acp-dev@<botname>
   ```

#### Workflow: Adding a New Dev Bot (Human / Interactive)
If a human is running the commands directly in the terminal, they can use the interactive CLI and pass `--no-start` to avoid interfering with the dev service:
```bash
uv run telegram-acp-client new <botname> --no-start
```

## 🛠 Troubleshooting & How-Tos

### Restarting a Stuck Session
If the agent is in a loop or unresponsive:
1. Try `/stop` first (sends an ACP `cancel` signal).
2. If that fails, use `/restart` (kills the process and starts a fresh one).

### Viewing Deep Logs
Set `LOG_LEVEL=DEBUG` in your config to see:
- Every raw ACP message exchanged with the agent.
- Detailed callback data for button clicks.
- Tracebacks for Markdown parsing failures.

### Inspecting Database
The SQLite database `{name}.db` can be inspected via CLI:
```bash
sqlite3 ~/.local/share/telegram-acp-client/default.db "SELECT * FROM messages WHERE session_id=1;"
```
Or use the provided `telegram_acp_client/read.py` utility.
