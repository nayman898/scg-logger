"""Receive-only serial transport. Port discovery reads OS metadata, not devices."""

import serial
from serial.tools import list_ports


def available_ports() -> list[dict[str, str]]:
    return [
        {"port": port.device, "name": port.description, "identity": port.hwid}
        for port in list_ports.comports()
    ]


class SerialSource:
    def __init__(self, port, identity):
        # Enumerates OS device metadata only; never opens other ports.
        found = next((p for p in list_ports.comports() if p.device == port), None)
        if found is None:
            raise OSError(f"{port} is not present")
        if identity and found.hwid != identity:
            raise OSError(f"{port} identity changed; select the adapter again")
        self.port = serial.Serial(
            port=None,
            baudrate=19200,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.1,
            write_timeout=0.1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.port.dtr = False
        self.port.rts = False
        self.port.port = port
        try:
            self.port.open()
        except Exception:
            self.port.close()
            raise

    def read(self):
        return self.port.read(min(max(self.port.in_waiting, 1), 512))

    def close(self):
        self.port.close()
