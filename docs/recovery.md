# Recording and recovery

## Process boundaries

The UI launches acquisition and export as separate processes. Acquisition owns
the serial handle, decoder, and database writer. Qt receives small JSON status
messages at roughly 10 Hz; the chart and event log have fixed history limits.
The database receives every decoded sample, independently of display updates.

On Windows, a virtual environment's `python.exe` redirects to another process.
The supervisor launches the underlying interpreter directly, with the project's
dependency path, so its process handle belongs to the worker that owns the port.
A regression test checks that those PIDs match.

## Retry limits

A 0.5-second pause inserts a gap and clears the partial packet buffer. After
3 seconds without bytes, acquisition closes the port and tries to reopen it.
Readings are not carried forward across a gap.

Reopen delays are 1, 2, 4, 8, and 16 seconds. Each attempt checks the selected COM
port and hardware identity. After 30 seconds of uninterrupted reception, a new
retry budget becomes available. Backoff waits send heartbeats and can be stopped.

If the worker doesn't report for 8 seconds, the supervisor terminates it. It may
restart the worker twice per session, resuming the same database with a recovery
marker. The monotonic origin and connection counter are retained. This is for
immediate worker recovery, not resuming a session after reboot.

Stop and window-close request a graceful exit. After 2 seconds, an unresponsive
worker is terminated without another restart. Exhausted retry budgets require an
explicit Start action.

Some adapters have no unique serial number. Their identity includes USB location,
so moving one to a different socket requires selection again. Identical adapters
swapped into the same socket cannot be distinguished this way.

## Storage

SQLite uses WAL and FULL synchronization. Each raw chunk and its decoded samples
are committed together. Schema version, decoder version, adapter identity, receive
UTC, and monotonic elapsed time are stored with the session.

A terminated writer may lose its last uncommitted chunk. Committed data remains
available for export. `closed_cleanly` records whether the writer finalized the
session; CSV exports expose this as `session_finalized`. False can mean either
an interrupted recording or a snapshot exported while recording was still active.

Windows may briefly retain journal handles after process termination. Database
opening retries transient I/O and lock errors with delays of 50, 100, 200, and
400 ms. Other errors are reported immediately.

Exports read a consistent database snapshot and write a temporary file beside
the destination, then replace the destination after flushing. A failed export
cleans up its temporary file. Terminating the export process can leave a hidden
`.partial` file; it can be removed and does not prevent another export.
