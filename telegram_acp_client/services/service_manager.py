import asyncio
import platform
import subprocess
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)

class ServiceManager(ABC):
    @abstractmethod
    async def start(self, name: str) -> bool: pass
    
    @abstractmethod
    async def stop(self, name: str) -> bool: pass
    
    @abstractmethod
    async def restart(self, name: str) -> bool: pass
    
    @abstractmethod
    async def status(self, name: str) -> str: pass
    
    @abstractmethod
    async def enable(self, name: str) -> bool: pass
    
    @abstractmethod
    async def disable(self, name: str) -> bool: pass

class ShellServiceManager(ServiceManager):
    """Implementation using system-level CLI commands (systemctl, launchctl, net/sc)."""
    
    def _run_cmd(self, cmd: list[str]) -> bool:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(cmd)} | Error: {e.stderr.decode()}")
            return False

    async def start(self, name: str) -> bool:
        if platform.system() == "Linux":
            return self._run_cmd(["systemctl", "--user", "start", f"telegram-acp-client@{name}.service"])
        return False

    async def stop(self, name: str) -> bool:
        if platform.system() == "Linux":
            return self._run_cmd(["systemctl", "--user", "stop", f"telegram-acp-client@{name}.service"])
        return False

    async def restart(self, name: str) -> bool:
        if platform.system() == "Linux":
            return self._run_cmd(["systemctl", "--user", "restart", f"telegram-acp-client@{name}.service"])
        return False

    async def status(self, name: str) -> str:
        if platform.system() == "Linux":
            try:
                res = subprocess.run(["systemctl", "--user", "status", f"telegram-acp-client@{name}.service"], capture_output=True, text=True)
                return res.stdout
            except Exception: return "Error getting status"
        return "Not implemented"

    async def enable(self, name: str) -> bool:
        if platform.system() == "Linux":
            return self._run_cmd(["systemctl", "--user", "enable", f"telegram-acp-client@{name}.service"])
        return False

    async def disable(self, name: str) -> bool:
        if platform.system() == "Linux":
            return self._run_cmd(["systemctl", "--user", "disable", f"telegram-acp-client@{name}.service"])
        return False

class NativeServiceManager(ServiceManager):
    """Implementation using platform-specific Python libraries (sdbus, pywin32, etc.)."""
    
    def __init__(self):
        self.system = platform.system()
        self._systemd = None
        if self.system == "Linux":
            try:
                from sdbus import SessionBus
                bus = SessionBus()
                self._systemd = bus.get_proxy("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
            except ImportError:
                logger.warning("sdbus not installed, NativeServiceManager will fall back to shell internally or fail")

    def _get_unit_name(self, name: str):
        return f"telegram-acp-client@{name}.service"

    async def start(self, name: str) -> bool:
        if self.system == "Linux" and self._systemd:
            # sdbus calls are typically blocking unless using the asyncio wrapper
            # StartUnit(name, mode)
            try:
                self._systemd.StartUnit(self._get_unit_name(name), "replace")
                return True
            except Exception as e:
                logger.error(f"Native StartUnit failed: {e}")
        return False

    async def stop(self, name: str) -> bool:
        if self.system == "Linux" and self._systemd:
            try:
                self._systemd.StopUnit(self._get_unit_name(name), "replace")
                return True
            except Exception as e:
                logger.error(f"Native StopUnit failed: {e}")
        return False

    async def restart(self, name: str) -> bool:
        if self.system == "Linux" and self._systemd:
            try:
                self._systemd.RestartUnit(self._get_unit_name(name), "replace")
                return True
            except Exception as e:
                logger.error(f"Native RestartUnit failed: {e}")
        return False

    async def status(self, name: str) -> str:
        if self.system == "Linux" and self._systemd:
            try:
                # This is a bit more complex natively, usually requires getting the unit object first
                unit_path = self._systemd.GetUnit(self._get_unit_name(name))
                # You'd then get a proxy for that path to check ActiveState etc.
                return f"Native status check for {name} successful (Unit path: {unit_path})"
            except Exception as e:
                return f"Native status check failed: {e}"
        return "Native status not supported on this platform yet"

    async def enable(self, name: str) -> bool:
        # Enable/Disable natively involves the 'Manager' interface 'EnableUnitFiles'
        return False

    async def disable(self, name: str) -> bool:
        return False

def get_service_manager(manager_type: str = "shell") -> ServiceManager:
    if manager_type == "native":
        return NativeServiceManager()
    return ShellServiceManager()
