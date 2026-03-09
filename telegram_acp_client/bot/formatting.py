import re
import difflib

def format_diff(old_text: str, new_text: str, path: str) -> str:
    """Generates a unified diff string."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    
    diff = difflib.unified_diff(
        old_lines, new_lines, 
        fromfile=f"a/{path}", 
        tofile=f"b/{path}",
        lineterm=""
    )
    result = "\n".join(diff)
    return result if result else "(No changes detected)"

def escape_markdown(text: str) -> str:
    """Helper to escape special characters for Telegram Markdown (v1)."""
    return re.sub(r"([_*`\[])", r"\\\1", text)

def is_approval_option(text: str) -> bool:
    """Checks if a button text or option ID represents an approval."""
    if not text: return False
    keywords = ["allow", "yes", "accept", "approve", "grant", "proceed", "confirm"]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)