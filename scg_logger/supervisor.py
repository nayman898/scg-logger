"""Start, stop and recover workers without blocking Qt's event loop."""

import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .paths import PROJECT_ROOT


class Supervisor(QObject):
    message = Signal(dict)
    ended = Signal(int)

    def __init__(self, parent=None, deadline=8.0, max_restarts=0):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        # Windows venv python.exe is a redirector which spawns another process.
        # Supervise the actual interpreter so kill() terminates the port owner.
        self.interpreter = (
            getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
        )
        if os.name == "nt":
            self.interpreter = str(Path(self.interpreter).with_name("python.exe"))
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert(
                "PYTHONPATH",
                os.pathsep.join(
                    [str(PROJECT_ROOT), str(Path(sys.prefix) / "Lib" / "site-packages")]
                ),
            )
            environment.insert("PYTHONNOUSERSITE", "1")
            self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(self.errors)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.error)
        self.buffer = bytearray()
        self.last = 0
        self.deadline = deadline
        self.stopping = None
        self.max_restarts = max_restarts
        self.restarts = 0
        self.restart_pending = False
        self.restart_requested = False
        self.terminal_message = False
        self.args = []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.watch)

    def start(self, args):
        self.args = list(args)
        self.restarts = 0
        self.restart_pending = False
        self.restart_requested = False
        self.launch()

    def launch(self):
        self.buffer.clear()
        self.last = time.monotonic()
        self.stopping = None
        self.terminal_message = False
        self.process.start(self.interpreter, ["-u", "-m", "scg_logger", *self.args])
        self.timer.start()

    def running(self):
        return self.restart_pending or self.process.state() != QProcess.NotRunning

    def read(self):
        self.buffer.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.buffer) > 65536:
            self.message.emit({"state": "failed", "detail": "Worker output exceeded limit"})
            self.process.kill()
            return
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("Worker messages must be objects")
                self.last = time.monotonic()
                if data.get("state") in ("stopped", "failed"):
                    self.terminal_message = True
                self.message.emit(data)
            except (ValueError, UnicodeError):
                self.message.emit({"detail": "Invalid worker output ignored"})

    def errors(self):
        error = bytes(self.process.readAllStandardError()).decode(errors="replace")[-4000:]
        self.message.emit({"detail": error})

    def stop(self):
        self.restart_requested = False
        if self.restart_pending:
            self.restart_pending = False
            self.ended.emit(0)
            return
        if self.running() and self.stopping is None:
            self.stopping = time.monotonic()
            self.process.write(b"stop\n")

    def watch(self):
        now = time.monotonic()
        if (
            self.stopping is not None and now - self.stopping > 2
        ) or now - self.last > self.deadline:
            self.timer.stop()
            self.restart_requested = (
                self.stopping is None
                and not self.terminal_message
                and self.restarts < self.max_restarts
                and self.args[:1] == ["worker"]
            )
            self.message.emit(
                {
                    "state": "failed",
                    "detail": "Worker timed out; terminated. Committed session data remains recoverable.",
                }
            )
            self.process.kill()

    def error(self, error):
        if error == QProcess.FailedToStart:
            self.timer.stop()
            self.message.emit({"state": "failed", "detail": self.process.errorString()})
            self.ended.emit(-1)

    def finished(self, code, _status):
        self.read()
        self.timer.stop()
        if self.restart_requested:
            self.restart_requested = False
            self.restart_pending = True
            self.restarts += 1
            self.message.emit(
                {
                    "state": "recovering",
                    "detail": f"Restarting serial worker {self.restarts}/{self.max_restarts}; continuing the same session",
                }
            )
            QTimer.singleShot(1000, self.restart)
            return
        self.ended.emit(code)

    def restart(self):
        if self.restart_pending:
            self.restart_pending = False
            if "--resume" not in self.args:
                self.args.append("--resume")
            self.launch()
