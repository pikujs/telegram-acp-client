import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from acp.schema import (
    CreateTerminalResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
)

logger = logging.getLogger(__name__)

class BackgroundTask:
    def __init__(self, task_id: str, command: str, proc: asyncio.subprocess.Process, on_log: Callable[[str], Any], session_id: int | None = None):
        self.task_id = task_id
        self.command = command
        self.proc = proc
        self.on_log = on_log
        self.session_id = session_id
        # Store last 1000 lines of logs
        self.log_buffer = deque(maxlen=1000)
        self._watcher_task = None

    async def start_watcher(self):
        self._watcher_task = asyncio.create_task(self._watch())

    async def _watch(self):
        try:
            # Initial 2 second flush
            await asyncio.sleep(2)
            await self.flush()

            # Every 5 seconds after
            while self.proc.returncode is None:
                await asyncio.sleep(5)
                await self.flush()

            # Final flush
            await self.flush()
            await self.on_log(f"🏁 Task `{self.task_id}` finished with code {self.proc.returncode}")
        except Exception:
            logger.exception(f"Error in watcher for {self.task_id}")

    async def flush(self):
        if not self.proc.stdout: return

        new_logs = []
        try:
            while True:
                # Read line without blocking too long
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=0.1)
                if not line: break
                decoded_line = line.decode().strip()
                if decoded_line:
                    new_logs.append(decoded_line)
                    self.log_buffer.append(decoded_line)
        except TimeoutError:
            pass

        if new_logs:
            await self.on_log(f"📋 *Logs for `{self.task_id}`*:\n" + "\n".join(new_logs))

    def get_last_logs(self, n: int) -> list[str]:
        logs = list(self.log_buffer)
        return logs[-n:]

class TerminalService:
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._cwd_registry: dict[int, str] = {} # chat_id -> current_path

    def get_cwd(self, chat_id: int, default_path: str) -> str:
        return self._cwd_registry.get(chat_id, default_path)

    def set_cwd(self, chat_id: int, path: str):
        self._cwd_registry[chat_id] = path

    async def run_shell(self, chat_id: int, command: str, cwd: str, on_log: Callable[[str], Any], session_id: int | None = None) -> str:
        task_id = f"job-{len(self._tasks) + 1}"

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )

        task = BackgroundTask(task_id, command, proc, on_log, session_id=session_id)
        self._tasks[task_id] = task
        await task.start_watcher()
        return task_id

    async def kill_all_in_session(self, session_id: int) -> int:
        killed_count = 0
        tasks_to_kill = [t for t in self._tasks.values() if t.session_id == session_id and t.proc.returncode is None]
        for task in tasks_to_kill:
            success = await self.kill_task(task.task_id)
            if success:
                killed_count += 1
        return killed_count

    def get_active_tasks(self) -> list[BackgroundTask]:
        # Filter for only running tasks
        return [t for t in self._tasks.values() if t.proc.returncode is None]

    async def kill_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.proc.returncode is None:
            logger.info(f"Terminal Service: Killing background task {task_id}")
            try:
                task.proc.terminate()
                try:
                    # Wait for graceful termination
                    await asyncio.wait_for(task.proc.wait(), timeout=2.0)
                    logger.info(f"Task {task_id} terminated gracefully")
                except TimeoutError:
                    logger.warning(f"Task {task_id} did not terminate in 2s, killing it")
                    task.proc.kill()
                    await task.proc.wait()
                    logger.info(f"Task {task_id} killed")
                return True
            except Exception as e:
                logger.error(f"Error killing task {task_id}: {e}")
                return False
        return False

    def get_logs(self, task_id: str, lines: int = 7) -> list[str] | None:
        task = self._tasks.get(task_id)
        if task:
            return task.get_last_logs(lines)
        return None

    # --- ACP Compatibility Methods ---
    async def create_terminal(self, command: str, session_id: str, args: list = None, cwd: str = None) -> CreateTerminalResponse:
        # Note: We need a chat_id to route logs. In ACP, we'll have to rely on the ActiveSession to provide it.
        # This will be handled in ACPService by overriding this call with a log callback.
        terminal_id = f"agent-term-{len(self._tasks) + 1}"
        logger.info(f"ACP Terminal creation requested for: {command}")
        # Real implementation of create_terminal is now deferred to ACPService logic
        # which will call run_shell once it has the telegram context.
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def get_output(self, terminal_id: str) -> TerminalOutputResponse:
        return TerminalOutputResponse(output="", truncated=False)

    async def release(self, terminal_id: str):
        pass

    async def wait_for_exit(self, terminal_id: str) -> WaitForTerminalExitResponse:
        return WaitForTerminalExitResponse()

    async def kill(self, terminal_id: str):
        await self.kill_task(terminal_id)

terminal_service = TerminalService()
