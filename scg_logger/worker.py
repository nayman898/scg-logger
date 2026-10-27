"""Serial, decoding and persistence live exclusively in this disposable process."""

import json
import math
import os
import sys
import threading
import time

from .protocol import ScgDecoder
from .store import Session
from .transport import SerialSource


def emit(**data):
    print(json.dumps({"worker_pid": os.getpid(), **data}, allow_nan=False), flush=True)


def run(
    path,
    port,
    *,
    identity="",
    demo=False,
    duration=None,
    stop=None,
    source_factory=SerialSource,
    retry_delays=(1, 2, 4, 8, 16),
    silence=3.0,
    stale_after=0.5,
    resume=False,
):
    stop = stop or threading.Event()
    started = time.monotonic()

    def elapsed():
        return time.monotonic() - started

    db = Session(
        path,
        {
            "port": port,
            "identity": identity,
            "demo": demo,
            "rpm": "disconnected",
            "decoder": "scg1-isp2-v1",
            "read_only": True,
            "serial": "19200 8N1; no flow control; DTR/RTS false",
            "timestamp_basis": "host receive UTC and monotonic elapsed seconds",
            "monotonic_start": started,
        },
        resume=resume,
    )
    started = db.origin
    source, connection, retries, count, raw_count = None, 0, 0, 0, 0
    if resume:
        connection, count, raw_count = db.counts()
    decoder = ScgDecoder()
    last_rx = last_emit = last_sample = time.monotonic()
    latest = None
    state = "starting"
    healthy_since = None

    def wait_retry(delay):
        deadline = time.monotonic() + delay
        while not stop.is_set() and time.monotonic() < deadline:
            emit(
                state="retrying",
                retry_in_s=round(max(0, deadline - time.monotonic()), 1),
                samples=count,
                bytes=raw_count,
            )
            stop.wait(min(0.25, max(0, deadline - time.monotonic())))

    def event(kind, detail):
        nonlocal state, latest
        state = kind
        latest = None
        db.event(elapsed(), kind, detail)
        if kind in ("gap", "disconnected", "stopped", "failed", "recovered"):
            db.append(
                elapsed(),
                connection,
                b"",
                [{"quality": kind, "source": "demo" if demo else "scg1"}],
            )
        emit(state=kind, detail=detail, samples=count, bytes=raw_count)

    def retry_connection(detail):
        nonlocal retries
        event("disconnected", detail)
        if retries >= len(retry_delays):
            event("failed", "Retry budget exhausted. Click Start to try again.")
            return False
        delay = retry_delays[retries]
        retries += 1
        event(
            "retrying",
            f"Retry {retries}/{len(retry_delays)} in {delay}s; same port and adapter only",
        )
        wait_retry(delay)
        return True

    try:
        if resume:
            event("recovered", "Resuming the same session after worker termination; gap preserved")
        event("starting", "SIMULATED DATA" if demo else f"Opening {port}; receive only")
        while not stop.is_set() and (duration is None or elapsed() < duration):
            if demo:
                t = elapsed()
                latest = {
                    "lambda": 0.94 + 0.08 * math.sin(t),
                    "boost_psi": 6 + 5 * math.sin(t / 3),
                    "wgdc_pct": 30 + 15 * math.sin(t / 3),
                    "quality": "simulated",
                    "source": "demo",
                }
                latest["afr"] = latest["lambda"] * 14.7
                db.append(t, 1, b"", [latest])
                count += 1
                last_sample = time.monotonic()
                state = "demo"
                stop.wait(0.08192)
            else:
                if source is None:
                    try:
                        source = source_factory(port, identity)
                        connection += 1
                        decoder.reset()
                        last_rx = time.monotonic()
                        healthy_since = last_rx
                        event("connected", f"{port} opened; waiting for data")
                    except (OSError, ValueError) as exc:
                        if not retry_connection(str(exc)):
                            break
                        continue
                try:
                    raw = source.read()
                    if raw:
                        last_rx = time.monotonic()
                        samples = decoder.feed(raw)
                        db.append(elapsed(), connection, raw, samples)
                        raw_count += len(raw)
                        count += len(samples)
                        if samples:
                            latest = samples[-1]
                            last_sample = time.monotonic()
                            state = "logging"
                            if healthy_since is None:
                                healthy_since = last_sample
                            if healthy_since is not None and last_sample - healthy_since >= 30:
                                retries = 0  # New budget only after sustained healthy reception.
                    elif time.monotonic() - last_rx >= silence:
                        raise OSError(f"No serial data for {silence:g}s")
                    elif time.monotonic() - last_rx >= stale_after and state != "gap":
                        healthy_since = None
                        decoder.reset()
                        event("gap", "Serial data paused; readings unavailable")
                except OSError as exc:
                    # Clear ownership before close so an exception cannot leave
                    # the loop trying to read a broken handle again.
                    broken_source, source = source, None
                    decoder.reset()
                    try:
                        broken_source.close()
                    except OSError as close_error:
                        event("close_error", str(close_error))
                    if not retry_connection(str(exc)):
                        break
            if time.monotonic() - last_emit >= 0.1:
                emit(
                    state=state,
                    samples=count,
                    bytes=raw_count,
                    latest=latest,
                    discarded=decoder.discarded,
                    elapsed_s=elapsed(),
                    sample_age_s=time.monotonic() - last_sample,
                )
                last_emit = time.monotonic()
        if state != "failed":
            event("stopped", "Session saved")
    except Exception as exc:
        emit(state="failed", detail=f"Logging stopped: {type(exc).__name__}: {exc}")
        db.close(clean=False)
        raise
    else:
        db.close()
    finally:
        if source is not None:
            source.close()


def stop_reader(stop):
    # stdin EOF also stops an orphaned worker if its UI disappears.
    sys.stdin.readline()
    stop.set()


def main(args):
    stop = threading.Event()
    threading.Thread(target=stop_reader, args=(stop,), daemon=True).start()
    run(
        args.session,
        args.port,
        identity=args.identity,
        demo=args.demo,
        duration=args.duration,
        stop=stop,
        resume=args.resume,
    )
