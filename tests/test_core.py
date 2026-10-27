import contextlib
import csv
import io
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scg_logger.protocol import ScgDecoder
from scg_logger.store import Session, export_csv
from scg_logger.worker import run

# Captured 2026-10-27, COM4, no TX. Not an encoder-generated fixture.
CAPTURED = bytes.fromhex("b28543131763000002030000")


class ProtocolTests(unittest.TestCase):
    def test_captured_frame_all_fragment_boundaries(self):
        for split in range(len(CAPTURED) + 1):
            d = ScgDecoder()
            samples = d.feed(CAPTURED[:split]) + d.feed(CAPTURED[split:])
            self.assertEqual(len(samples), 1)
            self.assertAlmostEqual(samples[0]["lambda"], 3.543)
            self.assertAlmostEqual(samples[0]["afr"], 52.0821)
            self.assertAlmostEqual(samples[0]["boost_psi"], 0.18426197458456)
            self.assertEqual(samples[0]["wgdc_pct"], 0)

    def test_warmup_and_error_do_not_become_afr(self):
        for function in range(1, 8):
            frame = bytearray(CAPTURED)
            frame[2] = 0x43 | (function << 2)
            sample = ScgDecoder().feed(frame)[0]
            self.assertNotIn("lambda", sample)
            self.assertNotIn("afr", sample)
            self.assertEqual(sample["wgdc_pct"], 0)

    def test_corruption_and_truncated_packet_resynchronize(self):
        d = ScgDecoder()
        samples = d.feed(b"noise" + CAPTURED[:7] + CAPTURED + CAPTURED)
        self.assertEqual(len(samples), 2)
        self.assertGreater(d.discarded, 0)
        for _ in range(100):
            d.feed(b"\xff" * 500)
        self.assertLessEqual(len(d.buffer), 130)

    def test_other_chain_and_responses_never_look_like_scg(self):
        self.assertEqual(ScgDecoder().feed(bytes.fromhex("a285") + CAPTURED[2:]), [])
        self.assertEqual(
            ScgDecoder().feed(bytes.fromhex("b2810000"))[0]["quality"], "unsupported_layout"
        )

    def test_full_scale_and_reset(self):
        frame = bytes.fromhex("b285431303740000077f077f")
        sample = ScgDecoder().feed(frame)[0]
        self.assertEqual(sample["lambda"], 1)
        self.assertEqual(sample["boost_psi"], 43.5)
        self.assertEqual(sample["wgdc_pct"], 100)
        d = ScgDecoder()
        d.feed(CAPTURED[:7])
        d.reset()
        self.assertEqual(len(d.feed(CAPTURED)), 1)


class StorageTests(unittest.TestCase):
    def test_failed_export_preserves_destination_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.sqlite"
            session = Session(path, {})
            session.append(0, 1, CAPTURED, ScgDecoder().feed(CAPTURED))
            session.close()
            output = Path(tmp) / "log.csv"
            output.write_text("previous export", encoding="utf-8")
            with patch("scg_logger.store.os.replace", side_effect=PermissionError("File in use")):
                with self.assertRaises(PermissionError):
                    export_csv(path, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous export")
            self.assertEqual(list(Path(tmp).glob("*.partial")), [])

    def test_export_cannot_replace_session_journals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.sqlite"
            session = Session(path, {})
            try:
                for suffix in ("", "-wal", "-shm"):
                    with self.assertRaises(ValueError):
                        export_csv(path, Path(str(path) + suffix))
            finally:
                session.close()

    def test_persistence_csv_and_incomplete_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite"
            s = Session(path, {"demo": False})
            s.append(0.25, 1, CAPTURED, ScgDecoder().feed(CAPTURED))
            s.append(0.5, 1, b"", [{"quality": "disconnected"}])
            # Independent reader sees committed data while writer remains open.
            output = Path(tmp) / "test.csv"
            export_csv(path, output)
            with output.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["lambda"], "")
            self.assertEqual(rows[1]["quality"], "disconnected")
            with self.assertRaises(FileExistsError):
                Session(path, {})
            with self.assertRaises(ValueError):
                export_csv(path, path)
            s.close(False)
            with contextlib.closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    db.execute("SELECT value FROM metadata WHERE key='closed_cleanly'").fetchone()[
                        0
                    ],
                    "false",
                )
                self.assertEqual(db.execute("SELECT payload FROM raw").fetchone()[0], CAPTURED)


class WorkerTests(unittest.TestCase):
    def test_close_error_does_not_prevent_reconnection(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event()
            attempts = []

            class Source:
                def __init__(self, *_):
                    self.index = len(attempts)
                    attempts.append(self)

                def read(self):
                    if self.index == 0:
                        raise OSError("Unplugged")
                    stop.set()
                    return CAPTURED

                def close(self):
                    if self.index == 0:
                        raise OSError("Handle no longer valid")

            path = Path(tmp) / "close-error.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                run(path, "COM4", stop=stop, source_factory=Source, retry_delays=(0,))
            with contextlib.closing(sqlite3.connect(path)) as db:
                self.assertEqual(len(attempts), 2)
                self.assertEqual(
                    db.execute("SELECT connection FROM samples WHERE quality='valid'").fetchall(),
                    [(2,)],
                )

    def test_retry_budget_and_gap_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempts = []

            def denied(port, identity):
                attempts.append(port)
                raise OSError("Access denied")

            with contextlib.redirect_stdout(io.StringIO()):
                run(Path(tmp) / "denied.sqlite", "COM4", source_factory=denied, retry_delays=(0, 0))
            self.assertEqual(attempts, ["COM4"] * 3)

    def test_disconnect_reconnect_preserves_session_and_resets_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event()
            connections = []

            class Source:
                def __init__(self, *_):
                    self.index = len(connections)
                    self.reads = 0
                    self.closed = False
                    connections.append(self)

                def read(self):
                    self.reads += 1
                    if self.index == 0:
                        if self.reads == 1:
                            return CAPTURED + CAPTURED[:6]
                        raise OSError("Unplugged")
                    stop.set()
                    return CAPTURED

                def close(self):
                    self.closed = True

            path = Path(tmp) / "reconnect.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                run(path, "COM4", stop=stop, source_factory=Source, retry_delays=(0,))
            self.assertTrue(all(s.closed for s in connections))
            with contextlib.closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT connection FROM samples WHERE quality='valid'").fetchall(),
                    [(1,), (2,)],
                )
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM events WHERE kind='disconnected'").fetchone()[
                        0
                    ],
                    1,
                )

    def test_no_data_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:

            class Empty:
                def __init__(self, *_):
                    pass

                def read(self):
                    return b""

                def close(self):
                    pass

            with contextlib.redirect_stdout(io.StringIO()):
                run(
                    Path(tmp) / "empty.sqlite",
                    "COM4",
                    source_factory=Empty,
                    silence=0,
                    retry_delays=(),
                )

    def test_short_pause_is_a_gap_without_reopening(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event()

            class Paused:
                def __init__(self, *_):
                    self.n = 0

                def read(self):
                    self.n += 1
                    if self.n == 2:
                        return b""
                    if self.n == 3:
                        stop.set()
                    return CAPTURED

                def close(self):
                    pass

            path = Path(tmp) / "pause.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                run(path, "COM4", stop=stop, source_factory=Paused, stale_after=0)
            with contextlib.closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("select quality from samples").fetchall(),
                    [("valid",), ("gap",), ("valid",), ("stopped",)],
                )
                self.assertEqual(
                    db.execute("select count(*) from events where kind='connected'").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
