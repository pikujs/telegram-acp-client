import asyncio
import os
import logging
from typing import Dict, List, Optional, Callable, Any, Union
from pathlib import Path

from acp import PROTOCOL_VERSION, Client, connect_to_agent, text_block
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    DeniedOutcome,
    EmbeddedResourceContentBlock,
    EnvVariable,
    FileSystemCapability,
    Implementation,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    ResourceContentBlock,
    TextContentBlock,
    ToolCall,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
    WriteTextFileResponse,
)
from telegram_acp_client.config import settings
from telegram_acp_client.services.terminal_service import terminal_service

logger = logging.getLogger(__name__)


class TelegramGeminiClient(Client):
    def __init__(
        self,
        on_text: Callable[[str], Any],
        on_permission: Callable[[ToolCall, List[PermissionOption]], Any],
        on_tool_start: Callable[[ToolCallStart], Any],
        on_thought: Optional[Callable[[str], Any]] = None,
        on_system_notification: Optional[Callable[[str], Any]] = None,
        on_terminal_request: Optional[
            Callable[[str, Optional[list], Optional[str]], Any]
        ] = None,
    ):
        super().__init__()
        self.on_text = on_text
        self.on_permission = on_permission
        self.on_tool_start = on_tool_start
        self.on_thought = on_thought
        self.on_system_notification = on_system_notification
        self.on_terminal_request = on_terminal_request

    async def _notify(self, text: str):
        logger.debug(f"System Notification: {text}")
        if self.on_system_notification:
            await self.on_system_notification(text)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        logger.debug(
            f"Received session update for session {session_id}: {type(update).__name__}"
        )
        try:
            if isinstance(update, AgentMessageChunk):
                text = self._extract_text(update.content)
                if text:
                    await self.on_text(text)
            elif isinstance(update, AgentThoughtChunk):
                text = self._extract_text(update.content)
                if text and self.on_thought:
                    await self.on_thought(text)
            elif isinstance(update, ToolCallStart):
                logger.debug(
                    f"Agent started tool call: {update.tool_call_id} ({update.title})"
                )
                await self.on_tool_start(update)
            elif isinstance(update, ToolCallProgress):
                status = update.status or "in progress"
                logger.debug(f"Tool call progress: {update.tool_call_id} -> {status}")
                msg = f"⏳ *Tool Update* (`{update.tool_call_id}`): {status}"
                if update.content:
                    for item in update.content:
                        if hasattr(item, "path"):  # FileEditToolCallContent
                            msg += f"\nFile: `{item.path}`"
                await self._notify(msg)
            elif isinstance(update, AgentPlanUpdate):
                logger.debug(f"Agent plan update with {len(update.entries)} entries")
                plan_text = "\n".join(
                    [f"- [{e.status}] {e.content}" for e in update.entries]
                )
                await self._notify(f"📋 *New Plan:*\n{plan_text}")
        except Exception as e:
            logger.exception(f"Error in session_update: {e}")

    def _extract_text(self, content: Any) -> Optional[str]:
        if isinstance(content, TextContentBlock):
            return content.text
        if isinstance(content, dict):
            return content.get("text")
        return None

    async def request_permission(
        self, options, session_id, tool_call, **kwargs
    ) -> RequestPermissionResponse:
        try:
            tc_id = getattr(
                tool_call, "tool_call_id", getattr(tool_call, "id", "unknown")
            )
            logger.debug(
                f"Permission requested for tool_call_id: {tc_id} ({tool_call.title})"
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
            raise RequestError.internal_error(str(e))

    async def read_text_file(self, path, session_id, **kwargs) -> ReadTextFileResponse:
        logger.info(f"AGENT READING FILE: {path}")
        try:
            content = Path(path).read_text()
            await self._notify(f"📖 *File Read:* `{path}`")
            return ReadTextFileResponse(content=content)
        except Exception as e:
            logger.exception(f"Error in read_text_file: {e}")
            raise RequestError.internal_error(str(e))

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

    async def ext_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"Extension Method called: {method} with params {params}")
        await self._notify(f"🔌 *Extension Method:* `{method}`\nParams: `{params}`")
        return {}

    async def ext_notification(self, method: str, params: Dict[str, Any]) -> None:
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
    def __init__(self, db_id, conn, acp_session, proc, client):
        self.db_id = db_id
        self.conn = conn
        self.acp_session = acp_session
        self.proc = proc
        self.client = client
        self.is_busy = False
        self.streamer = None
        # Registry for pending permissions
        # Format: { tool_call_id: { "future": Future, "options": { "1": "real_id", ... } } }
        self.permission_registry: Dict[str, Dict[str, Any]] = {}


class ACPService:
    def __init__(self):
        self.active_processes: Dict[int, ActiveSession] = {}

    async def start_session(
        self, db_id: int, path: str, client: TelegramGeminiClient
    ) -> ActiveSession:
        agent_cmd = settings.AGENT_COMMAND.split()

        # Change to target directory for the subprocess
        old_cwd = os.getcwd()
        os.chdir(path)
        try:
            proc = await asyncio.create_subprocess_exec(
                *agent_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,
            )
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("Failed to open pipes for agent process")

            conn = connect_to_agent(client, proc.stdin, proc.stdout)
            await conn.initialize(
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

            session = ActiveSession(db_id, conn, acp_session, proc, client)
            self.active_processes[db_id] = session
            return session
        finally:
            os.chdir(old_cwd)

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
                except asyncio.TimeoutError:
                    logger.warning(f"Agent session {db_id} did not terminate in 2s, killing it")
                    session.proc.kill()
                    await session.proc.wait()
                    logger.info(f"Agent session {db_id} killed successfully")


acp_service = ACPService()
