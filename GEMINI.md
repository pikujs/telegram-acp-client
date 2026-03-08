# GEMINI.md - Telegram ACP Client Development Guide

## 🏗 Architecture Overview

The application is built using an asynchronous, service-oriented architecture designed to handle long-running agent interactions and background shell tasks without blocking the Telegram update loop. Since v0.2.0, it supports **Multi-Tenant CLI management**, allowing multiple bot instances to run independently.

### Core Components
- **CLI Wrapper (`__main__.py`):** Handles bot lifecycle, service orchestration, and instance creation.
- **ACP Service (`services/acp_service.py`):** Manages the lifecycle of the agent subprocess. Uses `stdio` for communication and handles the Agent Client Protocol handshakes and tool requests.
- **Terminal Service (`services/terminal_service.py`):** Handles background shell execution. Maintains a log buffer for each task and manages per-chat CWD (Current Working Directory).
- **DB Service (`services/db_service.py`):** Uses `aiosqlite` for persistent storage. Database paths are dynamic based on the active bot instance.
- **Bot Modules (`bot/`):** Segregated command handlers and utility functions.
    - `agent.py`: Handles the core conversation and permission delegation.
    - `session.py`: Manages workspace lifecycles.
    - `process.py`: Handles system-level background jobs.
    - `utils.py`: Shared helpers like auth decorators and message splitters.

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
- Convenience wrappers around `systemctl --user` (no `sudo` required).
- Assumes the service template `telegram-acp-client@.service` is installed.

### `logs <name> [-f]`
- Wrapper around `journalctl --user -u telegram-acp-client@{name}.service`.

## 📜 Development Rules & Mandates

### 1. Concurrency & Deadlocks
- **Mandate:** NEVER await a blocking agent operation (like `conn.prompt`) directly inside a Telegram handler. 
- **Reason:** Telegram chat updates are processed sequentially. If you await a prompt that eventually requests a permission button click, you will deadlock the bot.
- **Solution:** Always wrap agent prompts in `asyncio.create_task()` within the handler.

### 2. Authorization
- **Mandate:** All handlers that interact with the system or agent must be protected.
- **Standard:** Use the `@authorized_only` decorator from `bot.utils`.

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
- **Rule:** Use `escape_markdown` for any variable text. Ensure code blocks are closed/opened correctly when splitting long messages.

### 6. Security Checks
- **Mandate:** Before committing any changes, the developer (or agent) must perform a manual audit of the staged diffs.
- **Checklist:**
    - No Telegram Bot Tokens (e.g., `123456:ABC...`).
    - No personal user names or absolute local paths (e.g., `/home/username/...`).
    - No hardcoded credentials or environment-specific values in the code.
    - Ensure `.gitignore` correctly covers any new sensitive files (like `.db` or `.json` config files).
- **Tool:** Use `git diff --staged` and search for common patterns like `TOKEN`, `KEY`, or `/home/`.

## 🔄 Core Workflows

### Creating a New Command
1. Add the handler function to the appropriate module in `bot/`.
2. Annotate with `@authorized_only`.
3. If it's long-running, wrap the logic in `async with typing_action(context, chat_id):`.
4. Register the command in `__main__.py`.
5. Update `HELP_TEXT` in `bot/common.py`.

### Agent Tool Approval Flow
1. Agent requests permission -> `acp_service.request_permission` is called.
2. Bot creates an `asyncio.Future` and registers it in `session.permission_registry`.
3. Bot sends buttons to the user (via `on_permission`).
4. User clicks button -> `handle_callback` is triggered.
5. `handle_callback` looks up the future, sets the result (or cancels the task), and updates the message.
6. The `acp_service` receives the result and returns it to the agent.

## 🛠 Local Development Setup

### 1. Development Systemd Service
For local development, use `telegram-acp-dev@.service`. This service uses `uv run` to execute the code directly from your project directory, ensuring that any changes you make are reflected after a service restart.

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

**Usage:**
- **Start:** `systemctl --user start telegram-acp-dev@{botname}`
- **Stop:** `systemctl --user stop telegram-acp-dev@{botname}`
- **Restart:** `systemctl --user restart telegram-acp-dev@{botname}`
- **Logs:** `journalctl --user -u telegram-acp-dev@{botname} -f`

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
