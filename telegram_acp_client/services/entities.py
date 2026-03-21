import logging
from typing import Any, List, Dict, Optional

logger = logging.getLogger(__name__)

class InteractionEntity:
    """Base class for all tracked interactions (text, tools, plans, etc.)"""

    def __init__(self, entity_id: str, kind: str):
        self.entity_id = entity_id
        self.kind = kind  # thought, message, tool, plan, mode
        self.status = "pending"
        self.messages = []  # List of Telegram Message objects or IDs
        self.data = {}

    def update(self, update_obj: Any) -> bool:
        """Merges update into state. Returns True if state actually changed."""
        return False

    def get_display_text(self) -> str:
        """Returns the formatted text for display in Telegram."""
        return f"*{self.kind.title()}:* {self.entity_id}"

class TextEntity(InteractionEntity):
    def __init__(self, entity_id: str, kind: str, role: str, prefix: str = ""):
        super().__init__(entity_id, kind)
        self.role = role
        self.prefix = prefix
        self.text = ""

    def update(self, text_chunk: str) -> bool:
        if not text_chunk:
            return False
        self.text += text_chunk
        return True

    def get_display_text(self) -> str:
        return self.prefix + self.text

class ToolEntity(InteractionEntity):
    def __init__(self, entity_id: str, kind: str):
        super().__init__(entity_id, kind)
        self.title = "unknown"
        self.tool_kind = "other"
        self.raw_input = {}
        self.content = []

    def update(self, update: Any) -> bool:
        changed = False
        # Update simple fields
        for field in ["title", "status"]:
            val = getattr(update, field, None)
            if val is not None and getattr(self, field) != val:
                setattr(self, field, val)
                changed = True

        # Tool kind
        kind = getattr(update, "kind", None)
        if kind is not None:
            if hasattr(kind, "value"):
                kind = kind.value
            if self.tool_kind != kind:
                self.tool_kind = kind
                changed = True

        # Merge raw_input (handles dict and object)
        ri = getattr(update, "raw_input", None) or (
            update.get("raw_input") if isinstance(update, dict) else None
        )
        if ri and isinstance(ri, dict):
            # Track if any keys actually changed or were added
            old_ri = self.raw_input.copy()
            self.raw_input.update(ri)
            if old_ri != self.raw_input:
                changed = True
            
            # Specialized command extraction for UI fallback
            if not self.data.get("command"):
                cmd = ri.get("command") or ri.get("cmd") or ri.get("script") or ri.get("code")
                if cmd: self.data["command"] = str(cmd)

        # Append content
        content = getattr(update, "content", None)
        if content:
            if isinstance(content, list):
                self.content.extend(content)
            else:
                self.content.append(content)
            changed = True
            
            # Specialized diff extraction for UI/Permission fallback
            for item in (content if isinstance(content, list) else [content]):
                if getattr(item, "type", None) == "diff" or (isinstance(item, dict) and item.get("type") == "diff"):
                    self.data["has_diff"] = True

        return changed

    def get_display_text(self) -> str:
        # Note: Actual formatting (emojis, etc.) is handled in agent.py 
        # using the format_tool_title logic, but we can provide a default here
        return f"🔧 *Tool:* {self.title} ({self.status})"

class PlanEntity(InteractionEntity):
    def __init__(self, entity_id: str):
        super().__init__(entity_id, "plan")
        self.entries = []

    def update(self, update: Any) -> bool:
        self.entries = getattr(update, "entries", [])
        return True

    def get_display_text(self) -> str:
        if not self.entries:
            return "📋 *Plan:* (empty)"
        lines = [f"- [{e.status}] {e.content}" for e in self.entries]
        return "📋 *New Plan:*\n" + "\n".join(lines)

class ModeEntity(InteractionEntity):
    def __init__(self, entity_id: str):
        super().__init__(entity_id, "mode")
        self.current_mode = "unknown"

    def update(self, update: Any) -> bool:
        mode = getattr(update, "mode", str(update))
        if self.current_mode != mode:
            self.current_mode = mode
            return True
        return False

    def get_display_text(self) -> str:
        return f"⚙️ *Mode Switched:* `{self.current_mode}`"

class UsageEntity(InteractionEntity):
    def __init__(self, entity_id: str):
        super().__init__(entity_id, "usage")
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def update(self, update: Any) -> bool:
        usage = getattr(update, "usage", update if isinstance(update, dict) else {})
        self.total_tokens = usage.get("total_tokens", self.total_tokens)
        self.prompt_tokens = usage.get("prompt_tokens", self.prompt_tokens)
        self.completion_tokens = usage.get("completion_tokens", self.completion_tokens)
        return True

    def get_display_text(self) -> str:
        return f"📊 *Token Usage:* {self.total_tokens} (Prompt: {self.prompt_tokens}, Completion: {self.completion_tokens})"
