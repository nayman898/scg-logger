"""Live dashboard and session controls."""

import math
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .paths import default_session_directory
from .supervisor import Supervisor


class Trend(QWidget):
    def __init__(self):
        super().__init__()
        self.points = deque(maxlen=600)
        self.setMinimumHeight(240)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#101b29"))
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor("#26384c"), 1))
        for i in range(1, 5):
            y = int(20 + (h - 50) * i / 5)
            p.drawLine(50, y, w - 20, y)
        points = list(self.points)
        lambda_max = max(
            2, math.ceil(max((s.get("lambda", 0) or 0 for _, s in points if s), default=2) * 2) / 2
        )
        series = [
            ("lambda", 0.5, lambda_max, "#51d4c0"),
            ("boost_psi", -15, 45, "#61a8ff"),
            ("wgdc_pct", 0, 100, "#f6b65f"),
        ]
        now = time.monotonic()
        for key, lo, hi, color in series:
            path = QPainterPath()
            connected = False
            for stamp, sample in points:
                if stamp < now - 60:
                    continue
                value = sample.get(key) if sample else None
                if value is None:
                    connected = False
                    continue
                x = 50 + (w - 70) * (1 - (now - stamp) / 60)
                y = 20 + (h - 50) * (1 - max(0, min(1, (value - lo) / (hi - lo))))
                if connected:
                    path.lineTo(x, y)
                else:
                    path.moveTo(x, y)
                connected = True
            p.setPen(QPen(QColor(color), 2))
            p.drawPath(path)
        p.setPen(QColor("#9bacc1"))
        p.drawText(
            50,
            h - 10,
            f"−60s     •     λ 0.5–{lambda_max:g}     /     boost −15–45 psi     /     WGDC 0–100%",
        )


class Window(QMainWindow):
    def __init__(self, session_dir=None, *, refresh_ports=True):
        super().__init__()
        self.setWindowTitle("SCG Logger")
        self.resize(1080, 760)
        self.session_dir = Path(session_dir) if session_dir else default_session_directory()
        self.closing = False
        self.last_sample = 0
        self.last_plot_count = -1
        self.session = None
        self.worker = Supervisor(self, max_restarts=2)
        self.worker.message.connect(self.message)
        self.worker.ended.connect(self.finished)
        self.helper = Supervisor(self, deadline=30)
        self.helper.message.connect(self.helper_message)
        self.helper.ended.connect(lambda _: self.refresh.setEnabled(True))
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel("SCG / LOGGER")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("INNOVATE SCG-1    ·    RECEIVE ONLY    ·    RPM DISCONNECTED"))
        controls = QHBoxLayout()
        self.ports = QComboBox()
        self.ports.setMinimumWidth(360)
        self.ports.addItem("Select your serial adapter…", None)
        self.refresh = QPushButton("Refresh ports")
        self.refresh.clicked.connect(self.list_ports)
        self.start = QPushButton("Start session")
        self.start.setObjectName("primary")
        self.start.clicked.connect(lambda: self.begin(False))
        self.demo = QPushButton("Try demo")
        self.demo.clicked.connect(lambda: self.begin(True))
        self.stop = QPushButton("Stop && save")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.worker.stop)
        for widget in (self.ports, self.refresh, self.start, self.demo, self.stop):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.status = QLabel("Ready · Select a port to begin")
        self.status.setObjectName("status")
        layout.addWidget(self.status)
        cards = QHBoxLayout()
        self.values = {}
        for key, name in [
            ("lambda", "LAMBDA"),
            ("afr", "AFR · device fuel multiplier"),
            ("boost_psi", "BOOST · psi"),
            ("wgdc_pct", "COMMANDED WGDC · %"),
        ]:
            card = QFrame()
            card.setObjectName("card")
            inner = QVBoxLayout(card)
            inner.addWidget(QLabel(name))
            value = QLabel("—")
            value.setObjectName("reading")
            inner.addWidget(value)
            self.values[key] = value
            cards.addWidget(card)
        layout.addLayout(cards)
        self.trend = Trend()
        layout.addWidget(self.trend, 1)
        self.stats = QLabel("0 samples · 0 bytes · awaiting session")
        layout.addWidget(self.stats)
        self.path = QLabel("Sessions are saved automatically while recording.")
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path.setWordWrap(True)
        layout.addWidget(self.path)
        export = QPushButton("Export saved session to CSV…")
        export.clicked.connect(self.export)
        layout.addWidget(export)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(150)
        self.log.setMaximumHeight(100)
        layout.addWidget(self.log)
        self.tick = QTimer(self)
        self.tick.setInterval(200)
        self.tick.timeout.connect(self.stale)
        self.tick.start()
        if refresh_ports:
            self.list_ports()

    def list_ports(self):
        if not self.helper.running():
            self.refresh.setEnabled(False)
            self.helper.start(["ports"])

    def helper_message(self, data):
        if "ports" in data:
            selected = self.ports.currentData()
            self.ports.clear()
            self.ports.addItem("Select your serial adapter…", None)
            for port in data["ports"]:
                self.ports.addItem(f"{port['port']} — {port['name']}", port)
                if port == selected:
                    self.ports.setCurrentIndex(self.ports.count() - 1)
        elif "exported" in data:
            self.log.appendPlainText("Exported: " + data["exported"])
        elif data.get("detail"):
            self.log.appendPlainText(data["detail"])

    def begin(self, demo):
        if self.worker.running():
            return
        port = self.ports.currentData()
        if not demo and port is None:
            self.status.setText("Select the SCG-1 adapter first")
            return
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.session = self.session_dir / (("demo-" if demo else "scg1-") + name + ".sqlite")
        args = ["worker", "--session", str(self.session)]
        args += ["--demo"] if demo else ["--port", port["port"], "--identity", port["identity"]]
        self.trend.points.clear()
        self.last_plot_count = -1
        self.clear_values()
        self.path.setText(str(self.session))
        self.start.setEnabled(False)
        self.demo.setEnabled(False)
        self.ports.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker.start(args)

    def clear_values(self):
        for value in self.values.values():
            value.setText("—")

    def message(self, data):
        if "state" in data:
            self.status.setText(data["state"].upper() + " · " + data.get("detail", ""))
        if "retry_in_s" in data:
            self.status.setText(
                f"RECONNECTING · Next attempt in {data['retry_in_s']:.1f}s · Stop remains available"
            )
        if data.get("detail"):
            self.log.appendPlainText(data["detail"])
        sample = data.get("latest")
        if sample:
            self.last_sample = time.monotonic() - data.get("sample_age_s", 0)
            for key, label in self.values.items():
                value = sample.get(key)
                label.setText(
                    "—" if value is None else f"{value:.3f}" if key == "lambda" else f"{value:.2f}"
                )
            self.status.setText(
                (
                    "DEMO · SIMULATED DATA"
                    if sample.get("source") == "demo"
                    else "LOGGING · " + sample["quality"]
                )
            )
            if data.get("samples") != self.last_plot_count:
                self.trend.points.append((time.monotonic(), sample))
                self.last_plot_count = data.get("samples")
        elif data.get("state") in (
            "gap",
            "disconnected",
            "retrying",
            "recovering",
            "recovered",
            "failed",
            "stopped",
        ):
            self.clear_values()
            self.trend.points.append((time.monotonic(), None))
        if "samples" in data:
            self.stats.setText(
                f"{data['samples']:,} samples · {data.get('bytes', 0):,} bytes · {data.get('discarded', 0)} discarded framing bytes"
            )

    def stale(self):
        if time.monotonic() - self.last_sample > 1:
            self.clear_values()
            self.trend.points.append((time.monotonic(), None))
            if self.status.text().startswith("LOGGING"):
                self.status.setText("STALE · Waiting for a valid packet")
        self.trend.update()

    def finished(self, code):
        self.start.setEnabled(True)
        self.demo.setEnabled(True)
        self.ports.setEnabled(True)
        self.stop.setEnabled(False)
        self.clear_values()
        if code:
            self.log.appendPlainText(
                "Worker ended unexpectedly. Existing committed data can still be exported."
            )
        if self.closing:
            QTimer.singleShot(0, self.close)

    def export(self):
        if self.helper.running():
            self.log.appendPlainText("Wait for the current background task to finish.")
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Choose session", str(self.session_dir), "Sessions (*.sqlite)"
        )
        if not source:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", str(Path(source).with_suffix(".csv")), "CSV (*.csv)"
        )
        if dest:
            self.helper.start(["export", source, dest])

    def closeEvent(self, event):
        if self.worker.running() or self.helper.running():
            self.closing = True
            self.worker.stop()
            self.helper.stop()
            event.ignore()
            QTimer.singleShot(250, self.close)
        else:
            event.accept()


def create_app():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { background: #0b131f; color: #dfe9f5; font: 10pt 'Segoe UI'; }
        QLabel#title { font-size: 24pt; font-weight: 700; }
        QLabel#status { color: #51d4c0; padding: 12px 0; }
        QFrame#card { background: #142235; border: 1px solid #25384f; border-radius: 10px; }
        QFrame#card QLabel { background: transparent; }
        QLabel#reading { font-size: 32pt; font-weight: 600; padding: 10px 0; }
        QPushButton, QComboBox { border: 1px solid #30445e; border-radius: 6px; padding: 9px; background: #18283c; }
        QPushButton:hover { background: #28425d; }
        QPushButton:disabled { color: #65758a; }
        QPushButton#primary { background: #167568; }
        QPlainTextEdit { border: 1px solid #25384f; padding: 6px; color: #a9bad0; }
    """)
    return app


def main():
    app = create_app()
    window = Window()
    window.show()
    app.exec()
