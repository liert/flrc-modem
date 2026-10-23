"""回调采集生命周期及在线输出；共享窗口契约见 test_realtime_buffers。"""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from flrc_modem.config import DemodConfig
from flrc_modem.io.iq import encode_samples
from flrc_modem.live_cli import main
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.pipeline import iter_frames
from flrc_modem.realtime import LiveReceiver, RealtimeBacklogError, RealtimeDecodeError
from flrc_modem.sdr.hackrf import HackRFConfig, HackRFDevice, StreamOverrun
from hackrf_fake import FakeAPI


CONFIG = DemodConfig(sample_rate=2_000_000, chunk_seconds=0.005)
HARDWARE = HackRFConfig(2_480_000_000, sample_rate=CONFIG.sample_rate)
FIXTURE = json.loads((Path(__file__).parent / "fixtures/dk32s_packet.json").read_text())


def signal_bytes():
    packet = encode_packet(bytes.fromhex(FIXTURE["payload_hex"]), bytes.fromhex(FIXTURE["sync_hex"]))
    modulation = ModulationConfig(sample_rate=CONFIG.sample_rate, repeat=3, guard_seconds=0.004)
    return b"".join(encode_samples(chunk, "cs8") for chunk in iter_signal(packet.bits, modulation))


class SessionTests(unittest.TestCase):
    def test_worker_crash_stops_capture_without_hanging(self):
        api = FakeAPI(rx_data=b"\0" * 400_000, buffer_size=4096, rx_interval=0.003)
        receiver = LiveReceiver(CONFIG, HARDWARE, device_factory=lambda: HackRFDevice(api))
        with self.assertRaises(RealtimeDecodeError), receiver:
            receiver._worker.process.terminate()
            receiver._worker.process.join(timeout=2)
            list(receiver.frames())
        self.assertEqual(receiver.stats.status, "failed")
        self.assertEqual(api.calls[-1], "release")
        self.assertIsNone(receiver._worker)

    @unittest.skipUnless(os.environ.get("FLRC_TEST_IQ"), "未设置外部 IQ 路径 FLRC_TEST_IQ")
    def test_recorded_packet_resampled_from_10m_to_2m_decodes_live(self):
        from scipy.signal import resample_poly
        from flrc_modem.io.iq import decode_samples
        with Path(os.environ["FLRC_TEST_IQ"]).open("rb") as source:
            iq = decode_samples(source.read(200_000), "cs8")
        # 通过抗混叠滤波转换真实录制数据，不能只修改采样率标签。
        raw = encode_samples(resample_poly(iq, 1, 5), "cs8")
        api = FakeAPI(rx_data=raw, buffer_size=4096, rx_interval=0.003)
        config = replace(CONFIG, chunk_seconds=0.002)
        receiver = LiveReceiver(config, HARDWARE, duration=len(raw) / 4_000_000,
                                device_factory=lambda: HackRFDevice(api))
        with receiver:
            frames = list(receiver.frames())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].packet.payload.hex(), FIXTURE["payload_hex"])
        self.assertAlmostEqual(frames[0].time_s, 0.00154, delta=0.00005)

    def test_frames_arrive_before_capture_ends_and_match_offline_at_2m(self):
        raw = signal_bytes()
        api = FakeAPI(rx_data=raw, buffer_size=4096, rx_interval=0.012)
        receiver = LiveReceiver(CONFIG, HARDWARE, duration=len(raw) / 4_000_000,
                                device_factory=lambda: HackRFDevice(api), window_queue_depth=32)
        with receiver:
            frames = receiver.frames()
            first = next(frames)
            self.assertFalse(receiver.done.is_set(), "不能等待全部采集结束才输出")
            actual = [first, *frames]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.iq"
            path.write_bytes(raw)
            expected = list(iter_frames(path, CONFIG))
        self.assertEqual(len(actual), 3)
        self.assertEqual(actual, expected)
        self.assertGreater(receiver.stats.decode.duplicate_frames, 0)
        self.assertEqual(receiver.stats.status, "completed")
        self.assertEqual(receiver.stats.input_samples, len(raw) // 2)
        self.assertEqual(api.calls[-2:], ["close", "release"])
        self.assertFalse(receiver._thread.is_alive())

    def test_continuous_capture_can_stop_and_drain(self):
        api = FakeAPI(rx_data=b"\0" * 400_000, buffer_size=4096, rx_interval=0.003)
        receiver = LiveReceiver(CONFIG, HARDWARE, device_factory=lambda: HackRFDevice(api))
        with receiver:
            timer = threading.Timer(0.08, receiver.stop)
            timer.start()
            try:
                self.assertEqual(list(receiver.frames()), [])
            finally:
                timer.cancel()
                timer.join()
        self.assertEqual(receiver.stats.status, "cancelled")
        self.assertGreater(receiver.stats.input_samples, 0)
        self.assertEqual(receiver.stats.capture["source_bytes"], receiver.stats.capture["written_bytes"])
        self.assertIn("stop_rx", api.calls)

    def test_periodic_firmware_overrun_stops_continuous_stream(self):
        api = FakeAPI(rx_data=b"\0" * 400_000, buffer_size=4096, rx_interval=0.003)
        device = HackRFDevice(api)
        device.state = lambda: {"num_shortfalls": 1, "error": 0}
        receiver = LiveReceiver(CONFIG, HARDWARE, device_factory=lambda: device, health_interval=0.01)
        with self.assertRaises(StreamOverrun), receiver:
            list(receiver.frames())
        self.assertEqual(receiver.stats.status, "failed")
        self.assertEqual(receiver.stats.hardware_shortfalls, 1)
        self.assertIn("stop_rx", api.calls)
        self.assertEqual(api.calls[-1], "release")

    def test_slow_decoder_reports_backlog_and_stops_native_rx(self):
        api = FakeAPI(rx_data=b"\0" * 400_000, buffer_size=4096, rx_interval=0.001)
        receiver = LiveReceiver(CONFIG, HARDWARE, device_factory=lambda: HackRFDevice(api),
                                window_queue_depth=2, dsp_process=False)
        def slow_decode(*args):
            time.sleep(0.08)
            return []
        receiver.decoder.decode = slow_decode
        with self.assertRaises(RealtimeBacklogError), receiver:
            list(receiver.frames())
        self.assertEqual(receiver.stats.status, "failed")
        self.assertIn("stop_rx", api.calls)
        self.assertEqual(api.calls[-1], "release")

    def test_output_failure_also_closes_device(self):
        api = FakeAPI(rx_data=signal_bytes(), buffer_size=4096, rx_interval=0.012)
        receiver = LiveReceiver(CONFIG, HARDWARE, device_factory=lambda: HackRFDevice(api))
        with self.assertRaisesRegex(OSError, "output failed"), receiver:
            next(receiver.frames())
            raise OSError("output failed")
        self.assertFalse(receiver._thread.is_alive())
        self.assertEqual(api.calls[-1], "release")


class LiveCLITests(unittest.TestCase):
    def test_auto_sample_rate_and_explicit_overrides(self):
        class EmptyReceiver:
            def __init__(self, config, hardware, **kwargs):
                self.config, self.hardware = config, hardware
                self.stats = SimpleNamespace(to_dict=lambda: {})
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def frames(self):
                return iter(())
        cases = [([], 2_000_000), (["--bit-rate", "650000"], 4_000_000),
                 (["--bit-rate", "1300000"], 6_000_000),
                 (["--sample-rate", "10000000"], 10_000_000),
                 (["--frequency", "2483060000"], 8_000_000)]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "live.toml"
            config_path.write_text("[demod]\nsample_rate = 4000000\n", encoding="utf-8")
            cases.append((["--config", str(config_path)], 4_000_000))
            for args, expected in cases:
                created = []
                def factory(*a, **kw):
                    created.append(EmptyReceiver(*a, **kw))
                    return created[-1]
                with self.subTest(args=args), patch("flrc_modem.live_cli.LiveReceiver", side_effect=factory), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), 0)
                self.assertEqual(created[0].config.sample_rate, expected)
                self.assertEqual(created[0].hardware.sample_rate, expected)

    def test_jsonl_and_builtin_parser_use_live_frames(self):
        api = FakeAPI(rx_data=signal_bytes(), buffer_size=4096, rx_interval=0.012)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.jsonl"
            with patch("flrc_modem.realtime.session.HackRFLibrary", return_value=api), \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                result = main(["--duration", str(len(api.rx_data) / 4_000_000), "--chunk-seconds", "0.005",
                               "--protocol", "dk32s", "--output", str(path), "--quiet"])
            self.assertEqual(result, 0)
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 3)
            self.assertTrue(all("application" in record for record in records))
            # 已有记录拒绝覆盖，且不会再次打开设备。
            opened = api.calls.count("open")
            with patch("flrc_modem.realtime.session.HackRFLibrary", return_value=api), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--output", str(path)]), 2)
            self.assertEqual(api.calls.count("open"), opened)
