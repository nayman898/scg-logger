import contextlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer

from scg_logger.store import export_csv, open_session
from scg_logger.supervisor import Supervisor
from scg_logger.ui import Window, create_app

APP = None


def setUpModule():
    global APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    APP = create_app()


def pump_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.005)
    return predicate()


class IsolationTests(unittest.TestCase):
    def test_stuck_child_is_killed_without_blocking_event_loop(self):
        s = Supervisor(deadline=0.3)
        messages, pulses, ended = [], [], []
        s.message.connect(messages.append)
        s.ended.connect(ended.append)
        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(lambda: pulses.append(1))
        timer.start()
        s.process.start(s.interpreter, ["-c", "import time; time.sleep(60)"])
        s.last = time.monotonic()
        s.timer.start()
        self.assertTrue(pump_until(lambda: bool(ended)))
        timer.stop()
        self.assertGreater(len(pulses), 5)
        self.assertTrue(any("timed out" in x.get("detail", "") for x in messages))

    def test_stop_deadline_kills_uncooperative_child(self):
        s = Supervisor()
        ended = []
        s.ended.connect(ended.append)
        s.process.start(s.interpreter, ["-c", "import time; time.sleep(60)"])
        s.last = time.monotonic()
        s.timer.start()
        s.stop()
        self.assertTrue(pump_until(lambda: bool(ended), 3.5))

    def test_demo_window_saves_and_closes(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        window = Window(session_dir=folder.name, refresh_ports=False)
        self.addCleanup(window.close)
        window.show()
        self.assertTrue(pump_until(lambda: not window.helper.running()))
        window.begin(True)
        self.assertTrue(pump_until(lambda: window.values["lambda"].text() != "—"))
        window.worker.stop()
        self.assertTrue(pump_until(lambda: not window.worker.running()))
        self.assertTrue(window.start.isEnabled())
        with contextlib.closing(sqlite3.connect(window.session)) as db:
            self.assertGreater(db.execute("select count(*) from samples").fetchone()[0], 0)
        window.close()

    def test_killed_writer_leaves_recoverable_committed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "killed.sqlite"
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                [str(Path.cwd()), str(Path(sys.prefix) / "Lib" / "site-packages")]
            )
            interpreter = getattr(sys, "_base_executable", sys.executable)
            proc = subprocess.Popen(
                [interpreter, "-u", "-m", "scg_logger", "worker", "--demo", "--session", str(path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            try:

                def committed():
                    if not path.exists():
                        return False
                    try:
                        with contextlib.closing(sqlite3.connect(path)) as db:
                            return db.execute("select count(*) from samples").fetchone()[0] > 2
                    except sqlite3.Error:
                        return False

                self.assertTrue(pump_until(committed))
            finally:
                proc.kill()
                proc.wait(timeout=3)
                proc.stdin.close()
            with contextlib.closing(open_session(path)) as db:
                self.assertEqual(db.execute("pragma integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    db.execute("select value from metadata where key='closed_cleanly'").fetchone()[
                        0
                    ],
                    "false",
                )
                self.assertGreater(db.execute("select count(*) from samples").fetchone()[0], 2)
            export_csv(path, Path(tmp) / "recovered.csv")

    def test_watchdog_restarts_worker_and_resumes_same_session(self):
        from scg_logger.store import Session

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.sqlite"
            db = Session(
                path,
                {
                    "port": "",
                    "identity": "",
                    "demo": True,
                    "decoder": "scg1-isp2-v1",
                    "monotonic_start": time.monotonic(),
                },
            )
            db.append(0.01, 1, b"", [{"source": "demo", "quality": "simulated", "lambda": 1}])
            db.close(False)
            s = Supervisor(deadline=3, max_restarts=1)
            messages = []
            s.message.connect(messages.append)
            s.args = ["worker", "--demo", "--session", str(path)]
            s.process.start(s.interpreter, ["-c", "import time; time.sleep(60)"])
            s.last = time.monotonic()
            s.timer.start()
            try:
                self.assertTrue(pump_until(lambda: any(m.get("latest") for m in messages), 10))
                self.assertEqual(s.restarts, 1)
                owner = next(m["worker_pid"] for m in reversed(messages) if m.get("latest"))
                self.assertEqual(owner, s.process.processId())
                self.assertTrue(any(m.get("state") == "recovered" for m in messages))
            finally:
                s.stop()
                self.assertTrue(pump_until(lambda: not s.running()))
            with contextlib.closing(open_session(path)) as db:
                self.assertEqual(
                    db.execute("select lambda from samples order by id limit 1").fetchone()[0], 1
                )
                times = [r[0] for r in db.execute("select elapsed_s from samples order by id")]
                self.assertEqual(times, sorted(times))

    def test_watchdog_restart_budget_is_finite(self):
        s = Supervisor(deadline=0.2, max_restarts=1)
        ended = []
        s.ended.connect(ended.append)

        def stuck_launch():
            s.last = time.monotonic()
            s.process.start(s.interpreter, ["-c", "import time; time.sleep(60)"])
            s.timer.start()

        s.launch = stuck_launch
        s.start(["worker", "--session", "unused"])
        self.assertTrue(pump_until(lambda: bool(ended), 4))
        self.assertEqual(s.restarts, 1)
