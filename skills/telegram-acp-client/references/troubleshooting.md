# Troubleshooting

## Checking Service Status
If a bot is not responding:
```bash
uv run telegram-acp-client status <name>
```
On Linux, this typically wraps `systemctl --user status telegram-acp-client@<name>`.

## Viewing Logs
Logs are the best way to diagnose connection issues or agent crashes.
```bash
uv run telegram-acp-client logs <name> -f
```
**Common Log Indicators:**
- `Successfully set bot commands`: Bot is connected and online.
- `No TELEGRAM_TOKEN found`: Missing or invalid token in config.
- `Error: [Errno 111] Connection refused`: The agent command failed to start.

## Database Corruption
If the database becomes corrupted, you can safely delete the `.db` file in the user data directory. The bot will recreate a fresh one on the next start (history will be lost).

## Restarting Everything
If the agent or bot gets into a weird state:
```bash
uv run telegram-acp-client restart <name>
```
