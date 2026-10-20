"""单元测试：统一 IQ 采集 (rx) 与发射 (tx) 命令行接口。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flrc_modem.rx_cli import make_argument_parser as make_rx_parser, main as rx_main, resolve_sdr_backend as resolve_rx_sdr
from flrc_modem.tx_cli import make_argument_parser as make_tx_parser, main as tx_main, resolve_sdr_backend as resolve_tx_sdr
from usrp_fake import FakeUHDLibrary
from hackrf_fake import FakeAPI
from flrc_modem.sdr.hackrf.library import HackRFLibrary


class UnifiedRxTxCLITests(unittest.TestCase):
    def test_rx_argument_parser_and_backend_resolution(self):
        parser = make_rx_parser()
        args = parser.parse_args([
            "--frequency", "2480000000",
            "--duration", "1.0",
            "--output", "outputs/test.cs8",
        ])
        self.assertEqual(args.frequency, 2480000000.0)
        self.assertEqual(args.duration, 1.0)
        self.assertEqual(resolve_rx_sdr(args), "usrp")

        args_usrp = parser.parse_args([
            "--sdr", "usrp",
            "--args", "addr=192.168.10.2",
            "--frequency", "2480000000",
            "--duration", "1.0",
            "--output", "outputs/test.cs8",
        ])
        self.assertEqual(resolve_rx_sdr(args_usrp), "usrp")

        args_hackrf = parser.parse_args([
            "--sdr", "hackrf",
            "--frequency", "2480000000",
            "--duration", "1.0",
            "--output", "outputs/test.cs8",
        ])
        self.assertEqual(resolve_rx_sdr(args_hackrf), "hackrf")

        args_auto_hackrf = parser.parse_args([
            "--serial", "0000000000000000000000000000abcd",
            "--frequency", "2480000000",
            "--duration", "1.0",
            "--output", "outputs/test.cs8",
        ])
        self.assertEqual(resolve_rx_sdr(args_auto_hackrf), "hackrf")

    def test_tx_argument_parser_and_backend_resolution(self):
        parser = make_tx_parser()
        args = parser.parse_args([
            "--iq", "outputs/test.cs8",
            "--frequency", "2480000000",
            "--repeat", "3",
        ])
        self.assertEqual(args.frequency, 2480000000.0)
        self.assertEqual(args.repeat, 3)
        self.assertEqual(resolve_tx_sdr(args), "usrp")

        args_hackrf = parser.parse_args([
            "--sdr", "hackrf",
            "--iq", "outputs/test.cs8",
            "--frequency", "2480000000",
        ])
        self.assertEqual(resolve_tx_sdr(args_hackrf), "hackrf")

    @patch("flrc_modem.rx_cli.UHDLibrary")
    @patch("flrc_modem.tx_cli.UHDLibrary")
    def test_usrp_unified_rx_and_tx_execution(self, mock_tx_lib, mock_rx_lib):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00\x01\x02\x03" * 250)
        mock_rx_lib.return_value = fake_lib
        mock_tx_lib.return_value = fake_lib

        with tempfile.TemporaryDirectory() as tmp_dir:
            iq_file = Path(tmp_dir) / "test_usrp.cs8"

            # 1. 执行统一 rx 命令采集
            rx_out = io.StringIO()
            with patch("sys.stdout", rx_out):
                code = rx_main([
                    "--sdr", "usrp",
                    "--args", "addr=192.168.10.2",
                    "--frequency", "2480000000",
                    "--sample-rate", "2500000",
                    "--gain", "30",
                    "--duration", "0.0001",
                    "--output", str(iq_file),
                ])
            self.assertEqual(code, 0)
            self.assertTrue(iq_file.is_file())
            self.assertGreater(len(iq_file.read_bytes()), 0)

            # 2. 执行统一 tx 命令发射
            tx_out = io.StringIO()
            with patch("sys.stdout", tx_out):
                tx_code = tx_main([
                    "--sdr", "usrp",
                    "--args", "addr=192.168.10.2",
                    "--iq", str(iq_file),
                    "--frequency", "2480000000",
                    "--sample-rate", "2500000",
                    "--gain", "30",
                    "--repeat", "2",
                ])
            self.assertEqual(tx_code, 0)
            stats = json.loads(tx_out.getvalue())
            self.assertEqual(stats["byte_count"], len(iq_file.read_bytes()) * 2)

    @patch("flrc_modem.rx_cli.HackRFLibrary")
    @patch("flrc_modem.tx_cli.HackRFLibrary")
    def test_hackrf_unified_rx_and_tx_execution(self, mock_tx_lib, mock_rx_lib):
        fake_api = FakeAPI(rx_data=b"\x11\x22" * 1024)
        mock_rx_lib.return_value = fake_api
        mock_tx_lib.return_value = fake_api

        with tempfile.TemporaryDirectory() as tmp_dir:
            iq_file = Path(tmp_dir) / "test_hackrf.cs8"

            # 1. 执行统一 rx 命令采集 (HackRF)
            rx_out = io.StringIO()
            with patch("sys.stdout", rx_out):
                code = rx_main([
                    "--sdr", "hackrf",
                    "--frequency", "2480000000",
                    "--sample-rate", "2000000",
                    "--gain", "30",
                    "--duration", "0.0005",
                    "--output", str(iq_file),
                ])
            self.assertEqual(code, 0)
            self.assertTrue(iq_file.is_file())
            self.assertGreater(len(iq_file.read_bytes()), 0)

            # 2. 执行统一 tx 命令发射 (HackRF)
            tx_out = io.StringIO()
            with patch("sys.stdout", tx_out):
                tx_code = tx_main([
                    "--sdr", "hackrf",
                    "--iq", str(iq_file),
                    "--frequency", "2480000000",
                    "--sample-rate", "2000000",
                    "--gain", "24",
                    "--repeat", "1",
                ])
            self.assertEqual(tx_code, 0)


if __name__ == "__main__":
    unittest.main()
