"""双设备就绪屏障、失败清理、真实身份校验与回传分类；不操作 USB。"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flrc_modem.exchange import ExchangeCapture, classify_record
from flrc_modem.exchange_cli import main
from flrc_modem.sdr.hackrf import HackRFConfig, StreamCancelled, StreamStats


class Device:
    def __init__(self, serial, shared):
        self.selected_device = {"index": 0 if serial == "tx" else 1, "serial": serial}
        self.shared = shared
        self.last_stats = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def receive(self, sink, config, **options):
        self.last_stats = StreamStats("rx", device_state={"error": 0, "num_shortfalls": self.shared.get("shortfalls", 0)})
        if self.shared.get("fail_rx"):
            raise OSError("模拟 RX 错误")
        while not options["cancel_event"].is_set():
            count = sink.write(b"\x01\x02" * 1000)
            self.last_stats.source_bytes += count
            self.last_stats.written_bytes += count
            self.shared["rx_bytes"] = self.last_stats.written_bytes
            options["cancel_event"].wait(0.001)
        raise StreamCancelled("正常取消采集")

    def transmit(self, source, config, **options):
        self.shared["tx_at_rx_bytes"] = self.shared.get("rx_bytes", 0)
        self.last_stats = StreamStats("tx")
        if self.shared.get("fail_tx"):
            raise OSError("模拟 TX 错误")
        block = bytearray(4096)
        while count := source.readinto(memoryview(block)):
            self.last_stats.source_bytes += count
        self.last_stats.flush_complete = True


class ExchangeTests(unittest.TestCase):
    def session(self, shared=None, **kwargs):
        shared = {} if shared is None else shared
        devices = []
        def factory(serial):
            device = Device(serial, shared)
            devices.append(device)
            return device
        session = ExchangeCapture(HackRFConfig(2480000000, sample_rate=2000000),
                                  HackRFConfig(2480000000, sample_rate=2000000),
                                  tx_serial="tx", rx_serial="rx", pre_roll=0.001,
                                  post_roll=0.005, timeout=1, device_factory=factory, **kwargs)
        return session, shared, devices

    def test_tx_waits_for_samples_and_preserves_post_roll(self):
        session, shared, devices = self.session()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.cs8"
            result = session.run(io.BytesIO(b"\x02\x03" * 1000), path)
            self.assertGreaterEqual(shared["tx_at_rx_bytes"], 4000)
            self.assertGreater(path.stat().st_size, shared["tx_at_rx_bytes"])
            self.assertEqual(result["rx"]["written_bytes"], path.stat().st_size)
            self.assertTrue(result["rx_ready_before_tx"])
            self.assertTrue(result["tx"]["flush_complete"])
        self.assertTrue(all(device.closed for device in devices))
        self.assertEqual(result["status"], "completed")

    def test_rx_failure_prevents_tx_and_closes_both_devices(self):
        session, shared, devices = self.session({"fail_rx": True})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(OSError, "RX"):
                session.run(io.BytesIO(b"test"), Path(directory) / "capture.cs8")
        self.assertNotIn("tx_at_rx_bytes", shared)
        self.assertTrue(all(device.closed for device in devices))
        self.assertEqual(session.stats["status"], "failed")

    def test_tx_failure_stops_rx_and_preserves_partial_capture(self):
        session, _, devices = self.session({"fail_tx": True})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.cs8"
            with self.assertRaisesRegex(OSError, "TX"):
                session.run(io.BytesIO(b"test"), path)
            self.assertGreater(path.stat().st_size, 0)
        self.assertTrue(all(device.closed for device in devices))
        self.assertEqual(session.stats["status"], "failed")

    def test_cancelled_rx_still_rejects_firmware_shortfalls(self):
        session, _, devices = self.session({"shortfalls": 1})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "固件状态"):
                session.run(io.BytesIO(b"test"), Path(directory) / "capture.cs8")
        self.assertEqual(session.stats["status"], "failed")
        self.assertTrue(all(device.closed for device in devices))

    def test_custom_transmission_runs_after_capture_ready(self):
        session, shared, _ = self.session()
        def transmit(device, cancel):
            device.transmit(io.BytesIO(b"test"), session.tx_config, cancel_event=cancel)
            return {"scheduled_slots": 1}
        with tempfile.TemporaryDirectory() as directory:
            session.run(None, Path(directory) / "capture.cs8", transmit_operation=transmit)
        self.assertGreaterEqual(shared["tx_at_rx_bytes"], 4000)
        self.assertEqual(session.stats["transmission"]["scheduled_slots"], 1)

    def test_same_device_alias_is_rejected_before_opening_rx(self):
        device = Device("000000tx", {})
        session = ExchangeCapture(HackRFConfig(2480000000), HackRFConfig(2480000000),
                                  tx_serial="tx", rx_serial="000000tx",
                                  device_factory=lambda serial: device)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "同一台"):
                session.run(io.BytesIO(b"test"), Path(directory) / "capture.cs8")
        self.assertTrue(device.closed)
        self.assertIsNone(device.last_stats)

    def test_existing_capture_is_never_overwritten(self):
        session, _, devices = self.session()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.cs8"
            path.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                session.run(io.BytesIO(b"test"), path)
            self.assertEqual(path.read_bytes(), b"keep")
        self.assertEqual(devices, [])

    def test_echo_ack_and_unrelated_sync_are_not_causal_reply_proof(self):
        sent = {("12345678", "aabbcc")}
        row = {"sync_word_hex": "12345678", "payload_hex": "aabbcc",
               "application": {"direction": "air_to_ground", "is_ack": True}}
        self.assertEqual(classify_record(row, sent), "tx_echo")
        row["payload_hex"] = "aabbdd"
        self.assertEqual(classify_record(row, sent), "air_to_ground_candidate")
        row["sync_word_hex"] = "87654321"
        self.assertEqual(classify_record(row, sent), "other")
        self.assertEqual(classify_record(row, sent, ("87654321",)), "air_to_ground_candidate")

    def test_cli_saves_capture_decode_and_no_false_reply_claim(self):
        def fake_run(session, source, path):
            # 用实际预生成 TX 波形模拟收到自己的发送，验证完整 CLI 自检和分类。
            block = bytearray(262144)
            with path.open("xb") as output:
                while count := source.readinto(memoryview(block)):
                    output.write(memoryview(block)[:count])
            session.stats["status"] = "completed"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            argv = ["--tx-serial", "tx", "--rx-serial", "rx", "--frequency", "2480000000",
                    "--sample-rate", "2000000", "--sync", "54455354",
                    "--payload-hex", "000102030405", "--repeat", "3", "--output-dir", str(output)]
            with patch.object(ExchangeCapture, "run", fake_run), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 0)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"], {"tx_echo": 3})
            self.assertTrue(report["link_verified"])
            self.assertFalse(report["causal_reply_verified"])
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 2)
            self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    unittest.main()
