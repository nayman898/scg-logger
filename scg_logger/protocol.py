"""Incremental ISP2 decoder. Standalone SCG-1 profile; no device writes.

See docs/protocol.md for evidence and validation status. Reject other chain layouts.
"""

from typing import Protocol

LAMBDA_STATES = (
    "valid",
    "oxygen",
    "calibrating",
    "needs_calibration",
    "warmup",
    "heater_calibration",
    "sensor_error",
    "reserved",
)


class Decoder(Protocol):
    def feed(self, data: bytes) -> list[dict]: ...
    def reset(self) -> None: ...


class ScgDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = 0

    def reset(self) -> None:
        self.buffer.clear()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        output = []
        while len(self.buffer) >= 2:
            a, b = self.buffer[:2]
            if a & 0xA2 != 0xA2 or not b & 0x80:
                del self.buffer[0]
                self.discarded += 1
                continue
            count = ((a & 1) << 7) | (b & 0x7F)
            size = 2 + count * 2
            if count == 0 or count > 64:
                del self.buffer[0]
                self.discarded += 1
                continue
            # Payload bytes must have MSB clear. A new header inside a partial
            # packet means corruption/truncation, not a reason to wait forever.
            available = self.buffer[2 : min(size, len(self.buffer))]
            if any(x & 0x80 for x in available):
                del self.buffer[0]
                self.discarded += 1
                continue
            if len(self.buffer) < size:
                break
            packet = bytes(self.buffer[:size])
            del self.buffer[:size]
            if not a & 0x10:  # Query response; never treat as sensor data.
                continue
            if count != 5:
                output.append({"quality": "unsupported_layout"})
                continue
            p = packet[2:]
            if p[0] & 0xE2 != 0x42 or p[2] & 0xC0 or any(p[i] & 0xF8 for i in (4, 6, 8)):
                output.append({"quality": "invalid_layout"})
                continue
            function = (p[0] >> 2) & 7
            value = (p[2] << 7) | p[3]
            multiplier = (((p[0] & 1) << 7) | p[1]) / 10
            sample = {
                "quality": LAMBDA_STATES[function],
                "sensor_value": value,
                "afr_multiplier": multiplier,
                "boost_raw": (p[6] << 7) | p[7],
                "wgdc_raw": (p[8] << 7) | p[9],
            }
            if function == 0:
                sample["lambda"] = value / 1000 + 0.5
                sample["afr"] = sample["lambda"] * multiplier
            # Vendor mts.dll AddPhysDevice maps SCG1 to AddBCG1Device:
            # BOOST endpoints -14.5..43.5 PSI; DUTY endpoints 0..100%.
            sample["boost_psi"] = sample["boost_raw"] * 58 / 1023 - 14.5
            sample["wgdc_pct"] = sample["wgdc_raw"] * 100 / 1023
            # RPM wire is disconnected; do not expose its zero as measured RPM.
            output.append(sample)
        return output
