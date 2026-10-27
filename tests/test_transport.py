import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scg_logger.transport import SerialSource, available_ports


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SimpleNamespace(device="COM4", description="Prolific", hwid="adapter-1")

    def test_port_list_does_not_open_any_ports(self):
        with (
            patch("scg_logger.transport.list_ports.comports", return_value=[self.adapter]),
            patch("scg_logger.transport.serial.Serial") as serial,
        ):
            self.assertEqual(
                available_ports(),
                [
                    {"port": "COM4", "name": "Prolific", "identity": "adapter-1"},
                ],
            )
            serial.assert_not_called()

    def test_changed_identity_is_rejected_before_open(self):
        with (
            patch("scg_logger.transport.list_ports.comports", return_value=[self.adapter]),
            patch("scg_logger.transport.serial.Serial") as serial,
        ):
            with self.assertRaises(OSError):
                SerialSource("COM4", "different-adapter")
            serial.assert_not_called()

    def test_reading_never_sends_a_command(self):
        port = Mock(in_waiting=12)
        port.read.return_value = b"sample"
        with (
            patch("scg_logger.transport.list_ports.comports", return_value=[self.adapter]),
            patch("scg_logger.transport.serial.Serial", return_value=port),
        ):
            source = SerialSource("COM4", "adapter-1")
            self.assertEqual(source.read(), b"sample")
            source.close()
        port.write.assert_not_called()
        port.read.assert_called_once_with(12)
        port.close.assert_called_once()
        self.assertFalse(port.dtr)
        self.assertFalse(port.rts)
