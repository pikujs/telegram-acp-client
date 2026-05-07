---
name: telegram-acp-client
description: "Manage and operate the Telegram ACP Client. Use this skill to setup new bots, manage multiple bot instances, update configurations, and troubleshoot service issues using the CLI."
---

# Telegram ACP Client

The Telegram ACP Client is a multi-tenant bridge between Telegram and ACP-compatible agents (like Gemini CLI). It allows users to interact with their system and agents directly from Telegram.

## Core Workflows

### 1. Provisioning a New Bot
To setup a new bot instance, use the `new` command. Note that in automated environments, you should create the config file manually to avoid interactive prompts.

**Interactive:**
```bash
uv run telegram-acp-client new <botname>
```

**Manual (Preferred for Agents):**
Create `<botname>.json` in the default config root (see [Config Schema](references/config_schema.md)).

### 2. Managing Multiple Bots
The client supports multiple independent bot instances (multi-tenancy). Each bot has its own config and database, identified by a unique `<name>`.

- **List active bots:** Check `~/.config/telegram-acp-client/` for JSON files.
- **Start a bot:** `uv run telegram-acp-client start <name>`
- **Stop a bot:** `uv run telegram-acp-client stop <name>`
- **Check Status:** `uv run telegram-acp-client status <name>`

See [Multi-Tenant Management](references/multi_tenant.md) for details.

### 3. Service Lifecycle
The client uses native OS service managers (systemd, launchd, etc.).

- **Enable autostart:** `uv run telegram-acp-client enable <name>`
- **Restart after changes:** `uv run telegram-acp-client restart <name>`
- **View Logs:** `uv run telegram-acp-client logs <name> -f`

See [CLI Reference](references/cli_reference.md) for all commands.

## References

- [CLI Reference](references/cli_reference.md) - Full command list and arguments.
- [Config Schema](references/config_schema.md) - JSON configuration structure.
- [Multi-Tenant Management](references/multi_tenant.md) - Handling multiple instances.
- [Troubleshooting](references/troubleshooting.md) - Logs, status, and common issues.
