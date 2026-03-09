# Telegram ACP Client 🤖

A modular multi-bot management system for the **[Agent Client Protocol (ACP)](https://agentclientprotocol.com/get-started/introduction)**. 

The Agent Client Protocol is an open standard designed to enable seamless communication between AI agents and their clients through a structured, JSON-RPC-based interface. It allows agents to perform complex tasks like file manipulation, terminal execution, and multi-step reasoning while maintaining a secure, human-in-the-loop approval workflow.

This project allows you to deploy and manage multiple independent Telegram bots, each acting as a client for an AI agent that supports the **Agent Client Protocol**. Any ACP-compatible agent can be used, including **gemini-cli**, **claude-code**, **codex**, and others.

---

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

### General Installation (Linux, macOS, Windows)
The recommended way to install `telegram-acp-client` is via `pipx`, which works on all platforms:

```bash
pipx install git+https://gitlab.pikujs.com/pikujs/telegram-acp-client.git
```

### Arch Linux (via AUR/PKGBUILD)
If you are on Arch Linux, you can install the package and its systemd service template using the provided `PKGBUILD`:

1. **Clone and build:**
   ```bash
   git clone https://gitlab.pikujs.com/pikujs/telegram-acp-client.git
   cd telegram-acp-client
   makepkg -si
   ```
   This installs the executable and places the systemd service template in `/usr/lib/systemd/user/`.

### Systemd Setup (Other Linux distros/Not installed from PKGBUILD)
To use the automated service management features on Linux, install the service template:
```bash
mkdir -p ~/.config/systemd/user/
cp telegram-acp-client@.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

---

## 🛠 Multi-Bot CLI Management

The `telegram-acp-client` command is your primary tool for managing bot instances.

### 1. Create a New Bot
```bash
telegram-acp-client new my-bot
```
This interactive command will:
- Create a `{name}.json` configuration in your platform's user config directory.
- Ask for your **Telegram Token** ([How to create a bot token](https://core.telegram.org/bots#how-do-i-create-a-bot)) and **Allowed Users**.
- Provide instructions for starting the bot.

### 2. Run a Bot
You can run a bot by its name (if it's in your config dir) or by providing an explicit path to a JSON config file:

**By Name:**
```bash
telegram-acp-client run my-bot
```

**By Config File:**
```bash
telegram-acp-client run --config ~/path/to/bot-config.json
```

### 3. Service Management (Cross-Platform)
You can manage your bots as background services automatically using native tools on Linux (`systemd`), macOS (`launchd`), and Windows (`schtasks`):
```bash
telegram-acp-client status my-bot
telegram-acp-client start my-bot
telegram-acp-client restart my-bot
telegram-acp-client stop my-bot
telegram-acp-client logs my-bot -f
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
        - `auth.py`: Strict whitelist-based security.
        - `formatting.py`: Functions for diffs and markdown escaping.
        - `messaging.py`: Core Telegram API wrappers with exponential backoff.
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
