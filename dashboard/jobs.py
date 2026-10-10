"""Run the screener from the dashboard: one run at a time, in the background, with its last output lines kept."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

TIMEOUT = 30 * 60  # seconds; a full refetch of a few hundred names takes about 20 minutes


def _console_python() -> str:
    """python.exe next to pythonw.exe: output capture is reliable with the console build; CREATE_NO_WINDOW hides it."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


class Runner:
    def __init__(self, root: Path, command: list[str] | None = None):
        self.root = Path(root)
        self.command = command or [_console_python(), "-m", "screener"]
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.lines: deque[str] = deque(maxlen=200)
        self.started = self.finished = None
        self.code: int | None = None

    def status(self) -> dict:
        with self._lock:
            return {"running": self._proc is not None and self.code is None,
                    "started": self.started, "finished": self.finished, "code": self.code, "log": list(self.lines)[-40:]}

    def start(self) -> bool:
        """False when a run is already going."""
        with self._lock:
            if self._proc is not None and self.code is None:
                return False
            self.lines.clear()
            self.code, self.finished = None, None
            self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # no console flashing up
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            self._proc = subprocess.Popen(self.command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                          creationflags=flags, env=env)
        threading.Thread(target=self._watch, args=(self._proc,), daemon=True).start()
        return True

    def _watch(self, proc: subprocess.Popen) -> None:
        timer = threading.Timer(TIMEOUT, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                with self._lock:
                    self.lines.append(line.rstrip())
            code = proc.wait()
        finally:
            timer.cancel()
        with self._lock:
            self.code = code
            self.finished = time.strftime("%Y-%m-%dT%H:%M:%S")
