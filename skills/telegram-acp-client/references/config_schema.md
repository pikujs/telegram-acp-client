# Configuration Schema

Bot configurations are stored as JSON files. By default, they are located in:
- **Linux:** `~/.config/telegram-acp-client/`
- **macOS:** `~/Library/Application Support/telegram-acp-client/`

## JSON Fields

| Field | Description | Default |
|-------|-------------|---------|
| `telegram_token` | The API token from @BotFather. | **Required** |
| `allowed_user_ids` | List of integers (Telegram User IDs) allowed to use the bot. | `[]` |
| `agent_command` | The command used to start the ACP agent. | `gemini --experimental-acp` |
| `user_projects_dir` | The starting directory for the bot's file/directory browser. | OS User Data Dir |
| `log_level` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` |
| `service_manager_type` | Type of service manager to use (`systemd`, `launchd`, `shell`). | `shell` |

## Example Config (`mybot.json`)
```json
{
    "telegram_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    "allowed_user_ids": [12345678],
    "agent_command": "gemini --experimental-acp",
    "user_projects_dir": "/home/pikujs/Projects",
    "log_level": "INFO"
}
```
