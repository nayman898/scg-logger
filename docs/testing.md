# Testing

## Automated tests

Run `python -m unittest discover -s tests -v` from the repo root. The suite covers
packet fragmentation and resynchronization, sensor status handling, channel
scales, transaction visibility, CSV output, bounded retries, and short gaps.
Qt tests also exercise hung workers, stop deadlines, and same-session recovery.
All sessions created by tests are temporary. No test opens a serial port.

## Hardware checks

The first hardware checks used Windows, an SCG-1, and a Prolific USB adapter
(VID 067B, PID 2303), with driver 3.3.3.114. The driver and controller settings
were left unchanged. RPM was disconnected throughout.

Live AFR agreed with the gauge at roughly 52.8–53. A short serial interruption
recovered. USB unplug/replug exposed both transient Windows errors and a stalled
reopen. The final recovery test replaced the stalled worker, reopened the port,
and saved more than 1,000 valid samples after reconnection in the same session.
The session finalized, passed SQLite's integrity check, and exported to CSV.

For another adapter or driver version:

1. Start a session on the explicitly selected COM port and compare the readings
   with the gauge. Don't change the controller's settings for this check.
2. Briefly disconnect the serial data cable. Check that readings become unavailable,
   a gap appears, and data resumes after reconnection.
3. Disconnect and reconnect USB. Check that Stop stays responsive, retry messages
   appear, and acquisition resumes in the same session. Allow time for Windows to
   re-enumerate the adapter.
4. Leave the adapter disconnected long enough to exhaust retries. Check that the
   app stops trying and that the saved session still exports.
5. Stop normally and export. Gaps should have blank readings, and the finalized
   flag should be true.

Full-range pressure, nonzero duty, extended drives, and disk-full behavior have
not yet been validated on hardware.

## LogWorks comparison

LogWorks was reported to hang after disconnect even while another process could
open the port. That suggests a lifecycle problem may persist after handle release,
but a hung thread-stack capture is needed to identify its cause. Offline inspection
found synchronous cleanup and indefinite waits in some SDK transport branches;
that alone does not prove which branch a Prolific connection takes.

SCG Logger does not load the LogWorks SDK. Its process boundary is intended to
contain a blocked transport operation regardless of where that operation stalls.
