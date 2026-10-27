"""Worker-owned transactional storage; no database work on the GUI thread."""

import csv
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Session:
    def __init__(self, path, metadata, resume=False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if resume:
            self.db = open_session(path, readonly=False)
            saved = {k: json.loads(v) for k, v in self.db.execute("SELECT key,value FROM metadata")}
            for key in ("port", "identity", "demo", "decoder"):
                if saved.get(key) != metadata[key]:
                    self.db.close()
                    raise ValueError(f"Cannot resume session: {key} differs")
            if saved.get("schema") != 1 or "monotonic_start" not in saved:
                self.db.close()
                raise ValueError("Cannot resume this session schema")
            self.origin = saved["monotonic_start"]
            self.db.execute("PRAGMA synchronous=FULL")
            with self.db:
                self.db.execute("UPDATE metadata SET value='false' WHERE key='closed_cleanly'")
            return
        # Exclusive creation avoids replacing a previous drive/session.
        with self.path.open("xb"):
            pass
        self.db = sqlite3.connect(self.path)
        self.origin = metadata.get("monotonic_start")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE raw(id INTEGER PRIMARY KEY, utc TEXT, elapsed_s REAL,
                             connection INTEGER, payload BLOB NOT NULL);
            CREATE TABLE samples(id INTEGER PRIMARY KEY, utc TEXT, elapsed_s REAL,
                source TEXT, connection INTEGER, lambda REAL, afr REAL,
                boost_psi REAL, wgdc_pct REAL, quality TEXT, raw_id INTEGER);
            CREATE TABLE events(id INTEGER PRIMARY KEY, utc TEXT, elapsed_s REAL,
                                kind TEXT, detail TEXT);
        """)
        with self.db:
            for k, v in {
                **metadata,
                "schema": 1,
                "started_utc": utc(),
                "closed_cleanly": False,
            }.items():
                self.db.execute("INSERT INTO metadata VALUES (?,?)", (k, json.dumps(v)))

    def event(self, elapsed, kind, detail):
        with self.db:
            self.db.execute(
                "INSERT INTO events VALUES(NULL,?,?,?,?)", (utc(), elapsed, kind, detail)
            )

    def append(self, elapsed, connection, raw, samples):
        stamp = utc()
        with self.db:
            raw_id = None
            if raw:
                raw_id = self.db.execute(
                    "INSERT INTO raw VALUES(NULL,?,?,?,?)", (stamp, elapsed, connection, raw)
                ).lastrowid
            for s in samples:
                self.db.execute(
                    "INSERT INTO samples VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                    (
                        stamp,
                        elapsed,
                        s.get("source", "scg1"),
                        connection,
                        s.get("lambda"),
                        s.get("afr"),
                        s.get("boost_psi"),
                        s.get("wgdc_pct"),
                        s["quality"],
                        raw_id,
                    ),
                )

    def close(self, clean=True):
        try:
            with self.db:
                self.db.execute(
                    "UPDATE metadata SET value=? WHERE key='closed_cleanly'", (json.dumps(clean),)
                )
        finally:
            self.db.close()

    def counts(self) -> tuple[int, int, int]:
        """Connection, decoded sample count and received bytes for worker recovery."""
        connection = self.db.execute("SELECT coalesce(max(connection),0) FROM samples").fetchone()[
            0
        ]
        samples = self.db.execute(
            "SELECT count(*) FROM samples "
            "WHERE quality NOT IN ('gap','disconnected','stopped','failed','recovered')"
        ).fetchone()[0]
        received = self.db.execute("SELECT coalesce(sum(length(payload)),0) FROM raw").fetchone()[0]
        return connection, samples, received


def open_session(database, readonly=True):
    """Windows can briefly retain WAL handles after TerminateProcess.

    Bound recovery attempts; never spin on corruption or an inaccessible file.
    Must be called in a worker, not on the UI thread.
    """
    delays = (0.05, 0.1, 0.2, 0.4)
    for attempt in range(len(delays) + 1):
        mode = "ro" if readonly else "rw"
        db = None
        try:
            db = sqlite3.connect(
                Path(database).resolve().as_uri() + f"?mode={mode}", uri=True, timeout=1
            )
            db.execute("SELECT count(*) FROM metadata").fetchone()
            return db
        except sqlite3.OperationalError as exc:
            if db is not None:
                db.close()
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if attempt == len(delays) or code not in (
                sqlite3.SQLITE_IOERR,
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            time.sleep(delays[attempt])


def export_csv(database, destination):
    """Consistent SQLite snapshot; crash-safe replacement, blanks for missing values."""
    database, destination = Path(database).resolve(), Path(destination).resolve()
    protected = {database, Path(str(database) + "-wal"), Path(str(database) + "-shm")}
    if destination in protected:
        raise ValueError("CSV destination cannot overwrite a session or its journal files")
    db = open_session(database)
    tmp = None
    try:
        db.execute("BEGIN")
        finalized = json.loads(
            db.execute("SELECT value FROM metadata WHERE key='closed_cleanly'").fetchone()[0]
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".partial",
            delete=False,
        ) as f:
            tmp = Path(f.name)
            w = csv.writer(f)
            w.writerow(
                [
                    "utc",
                    "elapsed_s",
                    "source",
                    "connection",
                    "lambda",
                    "afr",
                    "boost_psi",
                    "commanded_wgdc_pct",
                    "quality",
                    "session_finalized",
                ]
            )
            for row in db.execute(
                "SELECT utc,elapsed_s,source,connection,lambda,afr,boost_psi,wgdc_pct,quality FROM samples ORDER BY id"
            ):
                w.writerow(
                    [format(v, ".9g") if isinstance(v, float) else v for v in (*row, finalized)]
                )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destination)
    finally:
        db.close()
        if tmp is not None:
            tmp.unlink(missing_ok=True)
