# CLI Reference

The `telegram-acp-client` command provides several subcommands for managing bot instances.

## Subcommands

### `run [name] [--config PATH]`
Runs a bot instance in the foreground.
- `name`: (Optional) Name of the bot. Loads `<name>.json` from default config root. Defaults to `default`.
- `--config PATH`: Path to a specific JSON config file. Overrides `name`.

### `new [name] [--force] [--no-start]`
Interactively sets up a new bot configuration.
- `name`: (Optional) Name for the bot.
- `--force`: Overwrite existing config if it exists.
- `--no-start`: Do not automatically install/start the background service.

### `start <name>`
Starts the background service for the specified bot.

### `stop <name>`
Stops the background service for the specified bot.

### `restart <name>`
Restarts the background service for the specified bot.

### `status <name>`
Checks the status of the background service.

### `enable <name>`
Enables the background service to start on boot.

### `disable <name>`
Disables the background service from starting on boot.

### `logs <name> [-f]`
Views the logs for the specified bot instance.
- `-f, --follow`: Tail the logs in real-time.

## Usage with `uv`
Always prefer using `uv run` to execute the CLI within the repository:
```bash
uv run telegram-acp-client <command> [args]
```
