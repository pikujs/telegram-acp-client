import os
import sys

def get_user_shell() -> str:
    """
    Returns the user's configured shell.
    Prioritizes the SHELL environment variable, then falls back to the system password database,
    and finally defaults to 'bash' (or 'cmd.exe' on Windows).
    """
    if sys.platform == "win32":
        return os.environ.get("COMSPEC", "cmd.exe")

    # 1. Try SHELL environment variable
    shell = os.environ.get("SHELL")
    if shell:
        return shell

    # 2. Try passwd database (Unix only)
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell
    except (ImportError, KeyError, Exception):
        pass

    # 3. Fallback
    return "bash"
