import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, Client, connect_to_agent
from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    DeniedOutcome,
    EnvVariable,
    FileSystemCapability,
    Implementation,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    ToolCall,
    ToolCallProgress,
    ToolCallStart,
    WriteTextFileResponse,
)

from telegram_acp_client.config import settings
from telegram_acp_client.services.terminal_service import terminal_service

logger = logging.getLogger(__name__)


class TelegramGeminiClient(Client):
    def __init__(
        self,
        on_permission: Callable[[ToolCall, list[PermissionOption]], Any],
        on_tool_start: Callable[[ToolCallStart], Any],
        on_system_notification: Callable[[str], Any] | None = None,
        on_terminal_request: (
            Callable[[str, list | None, str | None], Any] | None
        ) = None,
        on_tool_update: Callable[[str, str, Any], Any] | None = None,
        on_permission_update: Callable[[str, Any, Any, list], Any] | None = None,
        on_entity_change: Callable[[str, str, str, str, str], Any] | None = None,
        on_entity_finished: Callable[[str, str], Any] | None = None,
    ):
        super().__init__()
        self.on_permission = on_permission
        self.on_tool_start = on_tool_start
        self.on_system_notification = on_system_notification
        self.on_terminal_request = on_terminal_request
        self.on_tool_update = on_tool_update
        self.on_permission_update = on_permission_update
        self.on_entity_change = on_entity_change
        self.on_entity_finished = on_entity_finished

        # Map update types to their respective handler methods
        self._HANDLERS = {
            "AgentMessageChunk": self._handle_message_chunk,
            "AgentThoughtChunk": self._handle_thought_chunk,
            "ToolCallStart": self._handle_tool_start,
            "ToolCallProgress": self._handle_tool_progress,
            "ToolCallUpdate": self._handle_tool_completion,
            "AgentPlanUpdate": self._handle_plan_update,
            "CurrentModeUpdate": self._handle_mode_update,
            "UsageUpdate": self._handle_usage_update,
            "AvailableCommandsUpdate": self._handle_available_commands,
        }

    async def _notify(self, text: str):
        logger.debug(f"System Notification: {text}")
        if self.on_system_notification:
            await self.on_system_notification(text)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        update_type = type(update).__name__

        # Check for dict-wrapped updates from some backends
        if isinstance(update, dict) and "sessionUpdate" in update:
            update_type = update["sessionUpdate"]
            # Map camelCase to PascalCase if needed
            update_type = update_type[0].upper() + update_type[1:]

        logger.info(f"Received session update [{session_id}]: {update_type}")
        logger.debug(f"Full update object: {update}")

        handler = self._HANDLERS.get(update_type)
        if handler:
            try:
                session = self._get_session_by_acp_id(session_id)
                if session:
                    # Sequential Processing: Use the session lock to prevent race conditions 
                    # during simultaneous Thought/Message/Tool updates.
                    async with session.update_lock:
                        await handler(session_id, update)
                else:
                    await handler(session_id, update)
            except Exception as e:
                logger.exception(f"Error in {update_type} handler: {e}")
        else:
            logger.debug(f"No handler for update type: {update_type}")

    def _get_session_by_acp_id(self, acp_id: str) -> "ActiveSession | None":
        for session in self.active_processes.values():
            if session.acp_session.session_id == acp_id:
                return session
        return None

    async def _handle_message_chunk(self, session_id: str, update: Any):
        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        # Surgical Finalization: Only close thoughts if we are starting a message
        if session.active_thought_id:
            await self._finalize_stream(session, "thought")

        content = getattr(update, "content", None) or (
            update.get("content") if isinstance(update, dict) else None
        )
        text = self._extract_text(content)
        if not text:
            return

        # Use a stable ID for the current message stream
        if not session.active_text_id:
            session.active_text_id = f"msg_{len(session.nodes)}"

        if self.on_entity_change:
            await self.on_entity_change(
                session_id, session.active_text_id, "message", "agent", ""
            )

        # Parallel Node Update
        if session.active_text_id in session.nodes:
            await session.nodes[session.active_text_id].apply(text)

    async def _handle_thought_chunk(self, session_id: str, update: Any):
        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        # Surgical Finalization: Only close messages if we are starting a thought
        if session.active_text_id:
            await self._finalize_stream(session, "message")

        content = getattr(update, "content", None) or (
            update.get("content") if isinstance(update, dict) else None
        )
        text = self._extract_text(content)
        if not text:
            return

        # Use a stable ID for the current thought stream
        if not session.active_thought_id:
            session.active_thought_id = f"thought_{len(session.nodes)}"

        if self.on_entity_change:
            await self.on_entity_change(
                session_id, session.active_thought_id, "thought", "thought", "💭 "
            )

        # Parallel Node Update
        if session.active_thought_id in session.nodes:
            await session.nodes[session.active_thought_id].apply(text)

    async def _handle_tool_start(self, session_id: str, update: Any):
        tc_id = getattr(update, "tool_call_id", "unknown")
        logger.info(f"Agent started tool call: {tc_id} ({update.title})")

        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        # Finalize any active text streams when a tool starts
        await self._finalize_active_streams(session)

        if self.on_entity_change:
            await self.on_entity_change(session_id, tc_id, "tool", "agent", "")

        # Node Update
        if tc_id in session.nodes:
            await session.nodes[tc_id].apply(update)

    async def _handle_tool_progress(self, session_id: str, update: Any):
        tc_id = getattr(update, "tool_call_id", "unknown")
        logger.info(
            f"Tool call progress: {tc_id} -> {getattr(update, 'status', 'in_progress')}"
        )

        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        if self.on_entity_change:
            await self.on_entity_change(session_id, tc_id, "tool", "agent", "")

        # Node Update
        if tc_id in session.nodes:
            await session.nodes[tc_id].apply(update)

    async def _handle_tool_completion(self, session_id: str, update: Any):
        tc_id = getattr(update, "tool_call_id", "unknown")
        logger.info(f"Tool call completion: {tc_id}")

        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        if self.on_entity_change:
            await self.on_entity_change(session_id, tc_id, "tool", "agent", "")

        # Node Update
        if tc_id in session.nodes:
            await session.nodes[tc_id].apply(update)

        if self.on_entity_finished:
            await self.on_entity_finished(session_id, tc_id)

    async def _finalize_active_streams(self, session: "ActiveSession"):
        """Closes all active text streams."""
        await self._finalize_stream(session, "thought")
        await self._finalize_stream(session, "message")

    async def _finalize_stream(self, session: "ActiveSession", kind: str):
        """Surgically finalizes a specific stream kind."""
        eid = (
            session.active_thought_id if kind == "thought" else session.active_text_id
        )
        if not eid:
            return

        # 1. Finalize the Node FIRST so it sends the permanent message bubble
        if eid in session.nodes:
            await session.nodes[eid].finalize()

        # 2. Notify the bot layer to cleanup
        if self.on_entity_finished:
            await self.on_entity_finished(session.acp_session.session_id, eid)

        # 3. Clear the ID
        if kind == "thought":
            session.active_thought_id = None
        else:
            session.active_text_id = None
            
        # 4. CRITICAL: Small delay to let Telegram process the permanent message 
        # bubble before any new draft starts in the same chat.
        await asyncio.sleep(0.5)

    async def _handle_plan_update(self, session_id: str, update: Any):
        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        if self.on_entity_change:
            await self.on_entity_change(session_id, "current_plan", "plan", "agent", "")

        # Node Update
        if "current_plan" in session.nodes:
            await session.nodes["current_plan"].apply(update)

    async def _handle_mode_update(self, session_id: str, update: Any):
        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        if self.on_entity_change:
            await self.on_entity_change(session_id, "current_mode", "mode", "agent", "")

        # Node Update
        if "current_mode" in session.nodes:
            await session.nodes["current_mode"].apply(update)

        # Compatibility with legacy modes logic
        mode = getattr(update, "mode", None)
        if (
            session.acp_session
            and hasattr(session.acp_session, "modes")
            and session.acp_session.modes
        ):
            session.acp_session.modes.current_mode_id = mode

    async def _handle_usage_update(self, session_id: str, update: Any):
        session = self._get_session_by_acp_id(session_id)
        if not session:
            return

        if self.on_entity_change:
            await self.on_entity_change(session_id, "usage_stats", "usage", "agent", "")

        # Node Update
        if "usage_stats" in session.nodes:
            await session.nodes["usage_stats"].apply(update)

    async def _handle_available_commands(self, session_id: str, update: Any):
        commands = getattr(update, "available_commands", [])
        logger.info(f"Available commands updated: {len(commands)} commands")
        session = self._get_session_by_acp_id(session_id)
        if session:
            session.available_commands = commands
            cmd_list = ", ".join([getattr(c, "name", str(c)) for c in commands[:10]])
            if len(commands) > 10:
                cmd_list += "..."
            await self._notify(f"🛠️ *Available Commands:* {cmd_list}")

    def _get_session_by_acp_id(self, session_id: str):
        for session in acp_service.active_processes.values():
            if session.acp_session.session_id == session_id:
                return session
        return None

    def _extract_text(self, content: Any) -> str | None:
        if content is None:
            return None

        # Handle string directly
        if isinstance(content, str):
            return content

        # Handle MCP/ACP ContentBlock structure
        c_type = getattr(content, "type", None) or (
            content.get("type") if isinstance(content, dict) else None
        )

        if c_type == "text":
            return getattr(content, "text", None) or (
                content.get("text") if isinstance(content, dict) else None
            )
        elif c_type == "image":
            mime = getattr(
                content, "mime_type", getattr(content, "mimeType", "image/*")
            ) or content.get("mimeType", "image/*")
            return f"🖼️ *[Image: {mime}]*"
        elif c_type == "audio":
            mime = getattr(
                content, "mime_type", getattr(content, "mimeType", "audio/*")
            ) or content.get("mimeType", "audio/*")
            return f"🎵 *[Audio: {mime}]*"
        elif c_type == "resource":
            res = getattr(content, "resource", None) or content.get("resource", {})
            uri = getattr(res, "uri", "unknown") or res.get("uri", "unknown")
            return f"📄 *[Resource: {uri}]*"
        elif c_type == "resource_link":
            uri = getattr(content, "uri", "unknown") or content.get("uri", "unknown")
            name = getattr(content, "name", "Resource") or content.get(
                "name", "Resource"
            )
            return f"🔗 *[{name}]({uri})*"

        # Fallback to standard attributes
        if hasattr(content, "text"):
            return content.text
        if isinstance(content, dict) and "text" in content:
            return content.get("text")

        return str(content) if content else None

    async def request_permission(
        self, options, session_id, tool_call, **kwargs
    ) -> RequestPermissionResponse:
        try:
            tc_id = getattr(
                tool_call, "tool_call_id", getattr(tool_call, "id", "unknown")
            )
            logger.info(
                f"Permission requested for tool_call_id: {tc_id} ({tool_call.title}) | ToolCall: {tool_call}"
            )

            selected_option = await self.on_permission(tool_call, options)
            if selected_option:
                logger.info(
                    f"PERMISSION GRANTED for tool {tc_id}: {selected_option.option_id}"
                )
                resp = RequestPermissionResponse(
                    outcome=AllowedOutcome(
                        option_id=selected_option.option_id, outcome="selected"
                    )
                )
                logger.debug(f"Sending AllowedOutcome to agent for {tc_id}")
                return resp

            logger.info(f"PERMISSION DENIED by user for tool {tc_id}")
            resp = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            logger.debug(f"Sending DeniedOutcome(cancelled) to agent for {tc_id}")
            return resp
        except Exception as e:
            logger.exception(f"Error in request_permission for {tc_id}: {e}")
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="error"))

    async def write_text_file(
        self, content, path, session_id, **kwargs
    ) -> WriteTextFileResponse:
        logger.info(f"AGENT WRITING FILE: {path} ({len(content)} bytes)")
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            await self._notify(f"💾 *File Written:* `{path}` ({len(content)} bytes)")
            return WriteTextFileResponse()
        except Exception as e:
            logger.exception(f"Error in write_text_file: {e}")
            raise RequestError.internal_error({"error": str(e)})

    async def read_text_file(self, path, session_id, **kwargs) -> ReadTextFileResponse:
        logger.info(f"AGENT READING FILE: {path}")
        try:
            p = Path(path)
            if not p.exists():
                logger.info(f"File not found, returning empty content: {path}")
                return ReadTextFileResponse(content="")
            content = p.read_text()
            await self._notify(f"📖 *File Read:* `{path}`")
            return ReadTextFileResponse(content=content)
        except Exception as e:
            logger.exception(f"Error in read_text_file: {e}")
            raise RequestError.internal_error({"error": str(e)})

    # --- Terminal Methods Delegated to TerminalService ---

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs,
    ) -> CreateTerminalResponse:
        logger.info(f"AGENT CREATING TERMINAL: {command} (cwd={cwd})")

        if self.on_terminal_request:
            # Trigger the real shell execution via terminal_service
            # This task_id will be returned to the agent and used for /ps
            task_id = await self.on_terminal_request(command, args, cwd)
            from acp.schema import CreateTerminalResponse

            return CreateTerminalResponse(terminal_id=task_id)

        await self._notify(f"🖥️ *Terminal Created:* `{command}`")
        return await terminal_service.create_terminal(command, session_id, args, cwd)

    async def terminal_output(self, session_id, terminal_id, **kwargs):
        return await terminal_service.get_output(terminal_id)

    async def release_terminal(self, session_id, terminal_id, **kwargs):
        logger.info(f"AGENT RELEASING TERMINAL: {terminal_id}")
        await self._notify(f"🔌 *Terminal Released:* `{terminal_id}`")
        await terminal_service.release(terminal_id)
        return None

    async def wait_for_terminal_exit(self, session_id, terminal_id, **kwargs):
        return await terminal_service.wait_for_exit(terminal_id)

    async def kill_terminal(self, session_id, terminal_id, **kwargs):
        logger.info(f"AGENT KILLING TERMINAL: {terminal_id}")
        await self._notify(f"🛑 *Terminal Killed:* `{terminal_id}`")
        await terminal_service.kill(terminal_id)
        return None

    # --- Extension Methods & Lifecycle ---

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.info(f"Extension Method called: {method} with params {params}")
        await self._notify(f"🔌 *Extension Method:* `{method}`\nParams: `{params}`")
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        logger.info(f"Extension Notification called: {method} with params {params}")
        await self._notify(
            f"🔔 *Extension Notification:* `{method}`\nParams: `{params}`"
        )

    def on_connect(self, conn: Any) -> None:
        logger.info("Agent connected")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify("🔗 *Agent Connected*"))
        except RuntimeError:
            logger.warning(
                "Could not send on_connect notification: no running event loop"
            )


class ActiveSession:
    def __init__(self, db_id, conn, acp_session, proc, client, agent_info=None):
        self.db_id = db_id
        self.conn = conn
        self.acp_session = acp_session
        self.proc = proc
        self.client = client
        self.agent_info = agent_info
        self.is_busy = False
        self.draft_streamer = None  # Set by bot layer
        self.available_commands = []
        self.update_lock = asyncio.Lock()  # Ensures sequential processing of session updates

        # Registry for UI-aware Nodes (owns state and UI)
        self.nodes: dict[str, Any] = {}
        self.permission_nodes: dict[str, Any] = {}  # tc_idx -> PermissionNode
        self.perm_counter = 0

        # IDs of currently active text streams
        self.active_thought_id: str | None = None
        self.active_text_id: str | None = None

    @property
    def is_alive(self) -> bool:
        """Checks if the agent process is running and the connection is not closed."""
        if self.proc.returncode is not None:
            return False
        # Accessing internal _closed attribute of acp.Connection
        return not getattr(self.conn, "_closed", True)


class ACPService:
    def __init__(self):
        self.active_processes: dict[int, ActiveSession] = {}

    async def start_session(
        self, db_id: int, path: str, client: TelegramGeminiClient
    ) -> ActiveSession:
        # Wrap the agent command in a login shell to ensure the user's environment
        # (like PATH for Nix or Homebrew) is fully loaded.
        agent_cmd = ["/bin/bash", "-l", "-c", settings.AGENT_COMMAND]

        try:
            proc = await asyncio.create_subprocess_exec(
                *agent_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,
                cwd=path,
            )
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("Failed to open pipes for agent process")

            # Increase the buffer limit for stdout to handle large messages (e.g., 1MB)
            # Default is 64KB, which can be exceeded by large tool outputs.
            if hasattr(proc.stdout, "_limit"):
                proc.stdout._limit = 1024 * 1024

            conn = connect_to_agent(client, proc.stdin, proc.stdout)
            init_resp = await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapability(read_text_file=True, write_text_file=True),
                    terminal=True,
                ),
                client_info=Implementation(
                    name="telegram-acp", title="Telegram ACP", version="0.1.0"
                ),
            )
            acp_session = await conn.new_session(cwd=path, mcp_servers=[])

            session = ActiveSession(
                db_id, conn, acp_session, proc, client, agent_info=init_resp
            )
            self.active_processes[db_id] = session
            return session
        except Exception as e:
            logger.error(f"Failed to start agent session: {e}")
            raise

    async def stop_session(self, db_id: int):
        if db_id in self.active_processes:
            session = self.active_processes.pop(db_id)
            if session.proc.returncode is None:
                logger.info(f"Stopping agent session {db_id} (terminating first)")
                session.proc.terminate()
                try:
                    # Give it 2 seconds to terminate gracefully
                    await asyncio.wait_for(session.proc.wait(), timeout=2.0)
                    logger.info(f"Agent session {db_id} terminated gracefully")
                except TimeoutError:
                    logger.warning(
                        f"Agent session {db_id} did not terminate in 2s, killing it"
                    )
                    session.proc.kill()
                    await session.proc.wait()
                    logger.info(f"Agent session {db_id} killed successfully")


acp_service = ACPService()
