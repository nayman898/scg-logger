# Contributing

Use Python 3.13 on Windows. Install the development dependencies and run the
commands in the README before opening a pull request.

## Code layout

- `protocol.py` parses ISP2 packets and converts the SCG-1 channels.
- `transport.py` reads serial data and lists the OS's port metadata.
- `worker.py` runs a recording session, including retries and gap detection.
- `store.py` owns the SQLite schema and CSV export.
- `supervisor.py` starts and stops background processes through Qt.
- `ui.py` renders the dashboard and handles user actions.

Keep serial access and database work out of the UI thread. Port enumeration must
not open or probe devices, and connection attempts must stay on the port the
user selected. This project is a logger: don't add configuration writes or tune
changes to the acquisition path.

When changing the decoder, add a captured or independently checked packet that
shows the behavior. Exercise split packets, malformed input, and status values,
not just the normal AFR path. Changes to recovery should also cover worker
termination and the readability of the saved session.

For new data sources, keep the source name, units, timestamp basis, and missing
value behavior explicit. Use a schema migration for new channels. Samples from
different devices should not be assumed simultaneous just because they share a
row number.

Run `python -m ruff format .` to format code. Comments should explain protocol
quirks or lifecycle decisions that aren't apparent from the code itself.

## Offline protocol research

`tools/inspect_vendor.py` inspects a locally installed `mts.dll` without loading
or running it. Its dependencies are separate from the app:

```powershell
.venv\Scripts\python.exe -m pip install -r tools/requirements-inspection.txt
.venv\Scripts\python.exe tools/inspect_vendor.py "C:\Program Files (x86)\LogWorks3\mts.dll"
```

Keep recordings, vendor binaries, and generated disassembly out of pull requests.
