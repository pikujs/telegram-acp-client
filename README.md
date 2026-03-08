# Telegram ACP Client 🤖

A modular multi-bot management system for the **Agent Client Protocol (ACP)**. This project allows you to deploy and manage multiple independent Telegram bots, each acting as a client for a coding agent (like `gemini-cli`), with separate configurations, databases, and systemd services.

## 🌟 Key Features

- **Multi-Tenant Management:** Deploy multiple bots from a single installation. Each bot has its own config, token, and workspace history.
- **Full ACP Support:** Handles handshakes, tool requests, and lifecycle management via `stdio`.
- **Human-in-the-Loop:** 
    - **Visual Diffs:** File edits are shown as unified diffs.
    - **Smart Splitting:** Large diffs/messages are automatically split into multiple formatted Markdown blocks.
    - **Persistent History:** Tool call details are preserved in the chat history after approval/rejection.
- **Advanced Terminal:**
    - Run background shell tasks (`/shell`).
    - Periodic log flushing to Telegram.
    - Inspect logs manually with `/logs <task_id>`.
    - Stop tasks with `/kill`.
- **Session Reliability:**
    - **Busy State:** Prevents sending overlapping prompts to the agent.
    - **Context Injection:** Manually restore conversation history via `/historyInject`.
    - **Auto-Reconnect:** Bots automatically reconnect to their agents on restart.
- **Security:** Strict whitelist-based authorization via Telegram usernames.

## 🚀 Installation

> [!IMPORTANT]
> **Multibot service management** and **background shell tasks** are only supported on **Linux machines with systemd**.

### Scenario 1: Arch Linux (via AUR/PKGBUILD)
If you are on Arch Linux, you can install the package and its systemd service template using the provided `PKGBUILD`:

1. **Clone the repository:**
   ```bash
   git clone https://gitlab.pikujs.com/pikujs/telegram-acp-client.git
   cd telegram-acp-client
   ```
2. **Build and install:**
   ```bash
   makepkg -si
   ```
   This will install the `telegram-acp-client` executable and place the systemd service template in `/usr/lib/systemd/user/`.

### Scenario 2: General Installation (Linux, macOS, Windows via pipx)
Use `pipx` to install the package in an isolated environment on any supported platform:

1. **Install the package:**
   ```bash
   pipx install git+https://gitlab.pikujs.com/pikujs/telegram-acp-client.git
   ```

2. **Install the systemd service template (Linux Only):**
   To use the multibot management features on Linux, you must manually install the service template to your user's systemd directory:
   ```bash
   mkdir -p ~/.config/systemd/user/
   cp telegram-acp-client@.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   ```

## 🛠 Multi-Bot CLI Management

The `telegram-acp-client` command is your primary tool for managing bot instances.

### 1. Create a New Bot
```bash
telegram-acp-client new my-bot
```
This interactive command will:
- Create a directory at `~/.config/telegram-acp-client/my-bot/`.
- Ask for your **Telegram Token** and **Allowed Users**.
- Save these to `bot.json`.
- Provide **instructions for installing the systemd user service**.

### 2. Run a Bot (Manual/Dev)
```bash
telegram-acp-client run --config ~/.config/telegram-acp-client/my-bot/
```

### 3. Service Management
Once you've installed the systemd user service template, you can manage your bots directly via the CLI (**no `sudo` required**):
```bash
telegram-acp-client status my-bot
telegram-acp-client start my-bot
telegram-acp-client restart my-bot
telegram-acp-client logs my-bot -f
telegram-acp-client stop my-bot
```

## ⚙️ Bot Config (`bot.json`)
Located in each bot's config directory:
```json
{
    "telegram_token": "YOUR_BOT_TOKEN",
    "allowed_users": ["your_username"],
    "agent_command": "gemini-cli",
    "log_level": "INFO"
}
```

## 📂 Project Structure

- `telegram_acp_client/`
    - `__main__.py`: CLI entry point and bot orchestration.
    - `bot/`: Segregated Telegram handlers.
        - `agent.py`: Core conversation & tool logic.
        - `session.py`: Workspace & Agent management.
        - `process.py`: Shell & background task management.
        - `navigation.py`: Local FS navigation.
        - `common.py`: Help & Start commands.
        - `utils.py`: Shared utilities (auth, diffs, splitting).
    - `services/`: Core application services.
        - `acp_service.py`: Protocol implementation.
        - `db_service.py`: Asynchronous SQLite persistence.
        - `terminal_service.py`: Background shell & log management.
    - `config.py`: Dynamic settings loading for multi-bot support.

## 🤖 In-Bot Commands
| Command | Description |
| :--- | :--- |
| `/start` / `/help` | Show help message. |
| `/new <name> <path>` | Create/Open a workspace session. |
| `/sessions` | List and switch workspaces. |
| `/restart` | Reset the agent in the current workspace. |
| `/stop` | Cancel the current agent task. |
| `/historyInject <n>` | Inject last `n` messages into context. |
| `/ls` / `/cd` | Navigate local files. |
| `/shell <cmd>` | Run a background process. |
| `/ps` / `/logs` / `/kill` | Manage background processes. |
