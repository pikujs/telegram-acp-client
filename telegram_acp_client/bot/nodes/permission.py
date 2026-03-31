import asyncio
import logging
from typing import Any, List, Optional, Dict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram_acp_client.bot.messaging import (
    send_safe_message,
    safe_edit,
    send_split_diff,
)
from telegram_acp_client.bot.formatting import (
    is_approval_option,
    escape_markdown,
    format_diff,
)

logger = logging.getLogger(__name__)


class PermissionNode:
    """
    Manages a single tool permission request lifecycle.
    Encapsulates the state (tool_call, options), the UI (message, buttons),
    and the completion (future).
    """

    def __init__(
        self, context, chat_id, session_id, tc_id, tc_idx, options, thread_id=None
    ):
        self.context = context
        self.chat_id = chat_id
        self.session_id = session_id
        self.tc_id = tc_id
        self.tc_idx = tc_idx  # Small stable index for callback data
        self.options = options
        self.thread_id = thread_id
        self.future = asyncio.Future()
        self.message = None

        # Map option_id -> option_metadata for quick lookup during click
        self.options_map = {
            opt.option_id: {
                "name": opt.name,
                "kind": getattr(opt, "kind", None) or "",
            }
            for opt in options
        }

    async def render(self, safe_title: str, tool_call: Any, node_state: Any = None):
        """Extracts diff info and sends the permission prompt with buttons."""

        diff_text = self._extract_diff(tool_call, node_state)

        btns = []
        for opt in self.options:
            o_kind = getattr(opt, "kind", None)
            if hasattr(o_kind, "value"):
                o_kind = o_kind.value
            emoji = "✅" if "allow" in (o_kind or "").lower() else "❌"
            if not o_kind:
                emoji = "✅" if is_approval_option(opt.name) else "❌"
            btns.append(
                [
                    InlineKeyboardButton(
                        f"{emoji} {opt.name}",
                        callback_data=(
                            "perm",
                            str(self.session_id),
                            str(self.tc_idx),
                            opt.option_id,
                        ),
                    )
                ]
            )

        prompt_text = f"🔐 *Permission Requested:*\n\n{safe_title}"

        # If there is a diff, send it separately to avoid UI clutter
        if diff_text:
            await send_split_diff(self.context, self.chat_id, diff_text, self.thread_id)
            prompt_text += "\n\n_(See proposed changes above)_"

        if not self.message:
            self.message = await send_safe_message(
                self.context,
                self.chat_id,
                prompt_text,
                reply_markup=InlineKeyboardMarkup(btns),
                message_thread_id=self.thread_id,
            )
        else:
            await safe_edit(
                self.message, prompt_text, reply_markup=InlineKeyboardMarkup(btns)
            )

    def _extract_diff(self, tool_call: Any, node_state: Any) -> str:
        """Ported logic to find a diff in tool_call content or node state."""
        diff_text = ""
        ri = getattr(tool_call, "raw_input", {}) or {}

        # 1. Search in content blocks
        content_sources = []
        if hasattr(tool_call, "content") and tool_call.content:
            content_sources.append(tool_call.content)
        if node_state and hasattr(node_state, "content") and node_state.content:
            content_sources.append(node_state.content)

        for content_list in content_sources:
            if diff_text:
                break
            for item in (
                content_list if isinstance(content_list, list) else [content_list]
            ):
                c_type = getattr(item, "type", None) or (
                    item.get("type") if isinstance(item, dict) else None
                )
                if c_type == "diff":
                    old = getattr(item, "oldText", getattr(item, "old_text", "")) or (
                        item.get("oldText") if isinstance(item, dict) else ""
                    )
                    new = getattr(item, "newText", getattr(item, "new_text", "")) or (
                        item.get("newText") if isinstance(item, dict) else ""
                    )
                    path = getattr(item, "path", "") or (
                        item.get("path") if isinstance(item, dict) else ""
                    )
                    return format_diff(str(old or ""), str(new or ""), str(path))

        # 2. Search in raw_input
        if ri:
            path = (
                ri.get("path")
                or ri.get("file_path")
                or ri.get("filePath")
                or ri.get("filepath")
                or "file"
            )
            if "diff" in ri:
                diff_text = str(ri.get("diff"))
                # Clean up Index/=== headers if it's a full patch
                lines = [
                    l
                    for l in diff_text.splitlines()
                    if not (l.startswith("Index: ") or l.startswith("======"))
                ]
                return "\n".join(lines).strip()

            old = (
                ri.get("oldText")
                or ri.get("oldString")
                or ri.get("old_string")
                or ri.get("old_text")
            )
            new = (
                ri.get("newText")
                or ri.get("newString")
                or ri.get("new_string")
                or ri.get("new_text")
            )
            if old is not None and new is not None:
                return format_diff(str(old), str(new), str(path))

        return ""

    async def handle_click(self, opt_id: str, session=None):
        """Processes a button click, sets the future, and updates the UI."""
        if self.future.done():
            return

        opt_data = self.options_map.get(opt_id, {"name": opt_id, "kind": ""})
        opt_name = opt_data.get("name", opt_id)
        opt_kind = opt_data.get("kind", "")

        is_approved = (
            is_approval_option(opt_name)
            or is_approval_option(opt_id)
            or is_approval_option(opt_kind)
        )

        original_text = self.message.text if self.message else "Permission Prompt"
        if len(original_text) > 3500:
            original_text = original_text[:3500] + "... [truncated]"

        if is_approved:
            from acp.schema import PermissionOption

            if not self.future.done():
                self.future.set_result(
                    PermissionOption(
                        option_id=opt_id, name="Allowed", kind="allow_once"
                    )
                )
            await safe_edit(
                self.message, f"{original_text}\n\n✅ *Granted*", parse_mode="Markdown"
            )
        else:
            if not self.future.done():
                self.future.set_result(None)

            if session:
                try:
                    await session.conn.cancel(session_id=session.acp_session.session_id)
                    await safe_edit(
                        self.message,
                        f"{original_text}\n\n❌ *Task Stopped & Permission Denied*",
                        parse_mode="Markdown",
                    )
                    return
                except Exception:
                    pass

            await safe_edit(
                self.message,
                f"{original_text}\n\n❌ *Permission Denied*",
                parse_mode="Markdown",
            )
