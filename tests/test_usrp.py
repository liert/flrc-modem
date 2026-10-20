"""USRP (UHD) 后端单元测试：覆盖配置、句柄、发现、收发流式传输与 CLI。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flrc_modem.sdr.usrp import (
    UHDError,
    USRPConfig,
    USRPDevice,
    discover_usrp_devices,
)
from flrc_modem.sdr.usrp.cli import main as usrp_cli_main
from usrp_fake import FakeUHDDll, FakeUHDLibrary


class USRPConfigTests(unittest.TestCase):
    def test_valid_config_and_format_normalization(self):
        cfg_cs8 = USRPConfig(frequency_hz=2480000000, sample_rate=2000000, cpu_format="cs8")
        self.assertEqual(cfg_cs8.cpu_format, "sc8")
        self.assertEqual(cfg_cs8.bytes_per_sample, 2)

        cfg_cf32 = USRPConfig(frequency_hz=2480000000, sample_rate=2000000, cpu_format="cf32")
        self.assertEqual(cfg_cf32.cpu_format, "fc32")
        self.assertEqual(cfg_cf32.bytes_per_sample, 8)

        cfg_dict = cfg_cs8.to_dict()
        self.assertEqual(cfg_dict["frequency_hz"], 2480000000)
        self.assertEqual(cfg_dict["cpu_format"], "sc8")

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            USRPConfig(frequency_hz=-100)
        with self.assertRaises(ValueError):
            USRPConfig(frequency_hz=2400000000, sample_rate=0)
        with self.assertRaises(ValueError):
            USRPConfig(frequency_hz=2400000000, gain=-5)
        with self.assertRaises(ValueError):
            USRPConfig(frequency_hz=2400000000, channel=-1)
        with self.assertRaises(ValueError):
            USRPConfig(frequency_hz=2400000000, cpu_format="invalid_format")


class USRPDeviceTests(unittest.TestCase):
    def test_open_close_and_device_info(self):
        fake_lib = FakeUHDLibrary()
        dev = USRPDevice(fake_lib, device_args="addr=192.168.10.2")

        with dev:
            self.assertEqual(dev.device_info["mboard_name"], "n310")
            self.assertEqual(dev.device_info["rx_channels"], 4)
            self.assertEqual(dev.device_info["tx_channels"], 4)
            pp_str = dev.get_pp_string()
            self.assertIn("N310", pp_str)

        self.assertIsNone(dev.handle.value)

    def test_double_open_rejected(self):
        fake_lib = FakeUHDLibrary()
        dev = USRPDevice(fake_lib)
        with dev:
            with self.assertRaises(UHDError):
                dev.__enter__()

    def test_configure_rx_and_tx(self):
        fake_lib = FakeUHDLibrary()
        dev = USRPDevice(fake_lib)
        with dev:
            cfg = USRPConfig(
                frequency_hz=2480000000,
                sample_rate=4000000,
                gain=25.0,
                channel=1,
                antenna="RX2",
                bandwidth_hz=3000000,
                clock_source="internal",
                time_source="internal",
            )
            rx_rate = dev.configure_rx(cfg)
            self.assertEqual(rx_rate, 4000000.0)
            self.assertIn(("set_rx_rate", 4000000.0, 1), fake_lib.dll.calls)
            self.assertIn(("set_rx_gain", 25.0, 1), fake_lib.dll.calls)
            self.assertIn(("set_rx_ant", b"RX2", 1), fake_lib.dll.calls)

            tx_cfg = USRPConfig(
                frequency_hz=2473660000,
                sample_rate=2000000,
                gain=35.0,
                channel=0,
                antenna="TX/RX",
            )
            tx_rate = dev.configure_tx(tx_cfg)
            self.assertEqual(tx_rate, 2000000.0)
            self.assertIn(("set_tx_rate", 2000000.0, 0), fake_lib.dll.calls)
            self.assertIn(("set_tx_gain", 35.0, 0), fake_lib.dll.calls)

    def test_receive_and_transmit_streaming(self):
        # 10 samples of cs8 (20 bytes)
        fake_samples = b"\x01\x02\x03\x04\x05\x06\x07\x08" * 5
        fake_lib = FakeUHDLibrary(rx_samples=fake_samples)
        dev = USRPDevice(fake_lib)

        cfg = USRPConfig(frequency_hz=2480000000, sample_rate=2000000)
        sink = io.BytesIO()

        with dev:
            stats = dev.receive(sink, cfg, sample_count=10, block_samples=4)
            self.assertEqual(stats.sample_count, 10)
            self.assertEqual(stats.byte_count, 20)
            self.assertEqual(sink.getvalue(), fake_samples[:20])

            # Test transmit
            source = io.BytesIO(b"\x10\x20\x30\x40" * 5)
            tx_stats = dev.transmit(source, cfg, block_samples=4)
            self.assertEqual(tx_stats.sample_count, 10)
            self.assertEqual(bytes(fake_lib.dll.tx_sent_bytes), b"\x10\x20\x30\x40" * 5)


class USRPDiscoveryTests(unittest.TestCase):
    def test_discover_devices_parsing(self):
        raw_device = "mgmt_addr=192.168.10.2,type=n3xx,product=n310,serial=3245E26,name=ni-n3xx-3245E26,fpga=HG"
        fake_lib = FakeUHDLibrary(devices=[raw_device])
        devices = discover_usrp_devices(fake_lib)
        self.assertEqual(len(devices), 1)
        dev = devices[0]
        self.assertEqual(dev["mgmt_addr"], "192.168.10.2")
        self.assertEqual(dev["product"], "n310")
        self.assertEqual(dev["serial"], "3245E26")
        self.assertEqual(dev["fpga"], "HG")


class USRPCLITests(unittest.TestCase):
    @patch("flrc_modem.sdr.usrp.cli.UHDLibrary")
    def test_cli_info_command(self, mock_lib):
        fake_lib = FakeUHDLibrary()
        mock_lib.return_value = fake_lib

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = usrp_cli_main(["info", "--args", "addr=192.168.10.2"])
            self.assertEqual(code, 0)

        data = json.loads(out.getvalue())
        self.assertEqual(data["library_version"], "4.9.0.0-fake")
        self.assertEqual(data["devices"][0]["product"], "n310")

    @patch("flrc_modem.sdr.usrp.cli.UHDLibrary")
    def test_cli_probe_command(self, mock_lib):
        fake_lib = FakeUHDLibrary()
        mock_lib.return_value = fake_lib

        out = io.StringIO()
        with patch("sys.stdout", out):
            code = usrp_cli_main(["probe", "--args", "addr=192.168.10.2"])
            self.assertEqual(code, 0)
        self.assertIn("Fake USRP N310 Tree Properties", out.getvalue())

    @patch("flrc_modem.sdr.usrp.cli.UHDLibrary")
    def test_cli_rx_and_tx_commands(self, mock_lib):
        fake_samples = b"\xaa\x55" * 100
        fake_lib = FakeUHDLibrary(rx_samples=fake_samples)
        mock_lib.return_value = fake_lib

        with tempfile.TemporaryDirectory() as tmpdir:
            rx_path = Path(tmpdir) / "rx.cs8"
            out = io.StringIO()
            with patch("sys.stdout", out):
                code = usrp_cli_main([
                    "rx",
                    "--args", "addr=192.168.10.2",
                    "--frequency", "2480000000",
                    "--sample-rate", "2000000",
                    "--duration", "0.00005",
                    "--output", str(rx_path),
                ])
                self.assertEqual(code, 0)
            self.assertTrue(rx_path.is_file())
            self.assertEqual(len(rx_path.read_bytes()), 200)

            # Test tx with that file
            tx_out = io.StringIO()
            with patch("sys.stdout", tx_out):
                tx_code = usrp_cli_main([
                    "tx",
                    "--args", "addr=192.168.10.2",
                    "--frequency", "2480000000",
                    "--sample-rate", "2000000",
                    "--iq", str(rx_path),
                    "--repeat", "2",
                ])
                self.assertEqual(tx_code, 0)
            tx_stats = json.loads(tx_out.getvalue())
            self.assertEqual(tx_stats["byte_count"], 400)


if __name__ == "__main__":
    unittest.main()
