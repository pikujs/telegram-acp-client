import os
import subprocess
import sys
from pathlib import Path


class OSServiceManager:
    def __init__(self, bot_name):
        from telegram_acp_client.config import settings

        self.bot_name = bot_name or "default"
        # Load settings specifically to resolve the correct data dir if needed
        # but avoid overriding already loaded configuration paths if bot_name matches
        if not hasattr(settings, 'bot_name') or settings.bot_name != self.bot_name:
            settings.load(bot_name=self.bot_name)

        self.data_dir = settings.DATA_DIR
        self.log_file = self.data_dir / f"{self.bot_name}.log"

    def install(self): raise NotImplementedError
    def start(self): raise NotImplementedError
    def stop(self): raise NotImplementedError
    def restart(self): raise NotImplementedError
    def status(self): raise NotImplementedError
    def enable(self): raise NotImplementedError
    def disable(self): raise NotImplementedError
    def logs(self, follow=False): raise NotImplementedError

class SystemdServiceManager(OSServiceManager):
    @property
    def service_name(self):
        return f"telegram-acp-client@{self.bot_name}.service"

    def install(self):
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_dir.mkdir(parents=True, exist_ok=True)
        template_path = service_dir / "telegram-acp-client@.service"

        if not template_path.exists():
            python_path = sys.executable
            template_content = f"""[Unit]
Description=Telegram ACP Client (%i)
After=network.target

[Service]
Type=simple
ExecStart={python_path} -m telegram_acp_client run %i
Restart=on-failure
RestartSec=5
WorkingDirectory={os.getcwd()}

[Install]
WantedBy=default.target
"""
            template_path.write_text(template_content)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            print(f"✅ Created systemd template at {template_path}")

    def start(self): subprocess.run(["systemctl", "--user", "start", self.service_name])
    def stop(self): subprocess.run(["systemctl", "--user", "stop", self.service_name])
    def restart(self): subprocess.run(["systemctl", "--user", "restart", self.service_name])
    def enable(self): subprocess.run(["systemctl", "--user", "enable", self.service_name])
    def disable(self): subprocess.run(["systemctl", "--user", "disable", self.service_name])
    def status(self): subprocess.run(["systemctl", "--user", "status", self.service_name])
    def logs(self, follow=False):
        cmd = ["journalctl", "--user", "-u", self.service_name]
        if follow: cmd.append("-f")
        subprocess.run(cmd)

class LaunchdServiceManager(OSServiceManager):
    @property
    def service_name(self):
        return f"com.telegram-acp-client.{self.bot_name}"

    @property
    def plist_path(self):
        return Path.home() / "Library" / "LaunchAgents" / f"{self.service_name}.plist"

    def install(self):
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.service_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>telegram_acp_client</string>
        <string>run</string>
        <string>{self.bot_name}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{self.log_file}</string>
    <key>StandardErrorPath</key>
    <string>{self.log_file}</string>
    <key>WorkingDirectory</key>
    <string>{os.getcwd()}</string>
</dict>
</plist>"""
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.plist_path.write_text(plist_content)
        print(f"✅ Created launchd agent at {self.plist_path}")

    def start(self):
        subprocess.run(["launchctl", "load", "-w", str(self.plist_path)])
        subprocess.run(["launchctl", "start", self.service_name])

    def stop(self):
        subprocess.run(["launchctl", "stop", self.service_name])

    def enable(self):
        subprocess.run(["launchctl", "load", "-w", str(self.plist_path)])

    def disable(self):
        subprocess.run(["launchctl", "unload", "-w", str(self.plist_path)])

    def restart(self):
        self.stop()
        self.start()

    def status(self):
        subprocess.run(["launchctl", "list", self.service_name])

    def logs(self, follow=False):
        if not self.log_file.exists():
            print(f"Log file {self.log_file} does not exist yet.")
            return
        cmd = ["tail"]
        if follow:
            cmd.append("-f")
        cmd.append(str(self.log_file))
        subprocess.run(cmd)

class WindowsServiceManager(OSServiceManager):
    @property
    def task_name(self):
        return f"TelegramACPClient_{self.bot_name}"

    def install(self):
        pythonw_exec = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw_exec):
            pythonw_exec = sys.executable

        cmd = [
            "schtasks", "/Create", "/F",
            "/TN", self.task_name,
            "/TR", f'"{pythonw_exec}" -m telegram_acp_client run {self.bot_name}',
            "/SC", "ONLOGON"
        ]
        subprocess.run(cmd, check=True)
        print(f"✅ Created Windows Scheduled Task '{self.task_name}'")

    def start(self):
        subprocess.run(["schtasks", "/Run", "/TN", self.task_name])

    def stop(self):
        ps_cmd = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match 'telegram_acp_client run {self.bot_name}' }} | Invoke-CimMethod -MethodName Terminate"
        subprocess.run(["powershell", "-Command", ps_cmd])

    def enable(self):
        subprocess.run(["schtasks", "/Change", "/TN", self.task_name, "/ENABLE"])

    def disable(self):
        subprocess.run(["schtasks", "/Change", "/TN", self.task_name, "/DISABLE"])

    def restart(self):
        self.stop()
        self.start()

    def status(self):
        subprocess.run(["schtasks", "/Query", "/TN", self.task_name])

    def logs(self, follow=False):
        if not self.log_file.exists():
            print(f"Log file {self.log_file} does not exist yet.")
            return
        if follow:
            subprocess.run(["powershell", "-Command", f"Get-Content '{self.log_file}' -Wait -Tail 100"])
        else:
            subprocess.run(["powershell", "-Command", f"Get-Content '{self.log_file}' -Tail 100"])

def get_manager(bot_name: str) -> OSServiceManager:
    if sys.platform == "linux":
        return SystemdServiceManager(bot_name)
    elif sys.platform == "darwin":
        return LaunchdServiceManager(bot_name)
    elif sys.platform == "win32":
        return WindowsServiceManager(bot_name)
    else:
        raise NotImplementedError(f"Unsupported OS: {sys.platform}")
