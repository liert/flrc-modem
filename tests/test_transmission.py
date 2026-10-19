"""不落盘发送的物理回环、动态队列、CLI 与设备生命周期。"""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from flrc_modem.config import DemodConfig
from flrc_modem.io.iq import decode_samples
from flrc_modem.modulation import ModulationConfig
from flrc_modem.pipeline import WindowDecoder
from flrc_modem.sdr.hackrf import HackRFConfig, HackRFDevice, StreamCancelled, StreamUnderrun
from flrc_modem.transmission import LiveTransmitter, PacketSource
from flrc_modem.transmission.input import iter_json_records
from flrc_modem.transmit_cli import main
from hackrf_fake import FakeAPI

MODULATION = ModulationConfig(sample_rate=2_000_000, guard_seconds=0.003, gap_seconds=0.004)
HARDWARE = HackRFConfig(2_480_000_000, sample_rate=MODULATION.sample_rate)
PAYLOAD_A, PAYLOAD_B = b"\x00\x01\x02\x03\x04\x05", bytes(range(20))


def collect(source, size=4096):
    destination = bytearray(size)
    output = bytearray()
    while count := source.readinto(memoryview(destination)):
        output.extend(destination[:count])
    return bytes(output)


def decode(raw, modulation=MODULATION):
    config = DemodConfig(sample_rate=modulation.sample_rate, coding_rate=modulation.coding_rate,
                         bt=modulation.bt, bit_rate=modulation.bit_rate,
                         frequencies=(HARDWARE.frequency_hz + modulation.offset_hz,),
                         filter_cutoff=modulation.bit_rate * 350 / 325)
    return list(WindowDecoder(config).decode(decode_samples(raw, "cs8"), 0))


class PacketSourceTests(unittest.TestCase):
    def test_dynamic_packets_after_idle_roundtrip_without_files(self):
        source = PacketSource(MODULATION, b"TEST")
        idle = bytearray(4096)
        self.assertEqual(source.readinto(memoryview(idle)), len(idle))
        self.assertEqual(idle, bytes(len(idle)))
        source.submit(PAYLOAD_A)
        source.submit(PAYLOAD_B, sync_word=b"NEXT", repeat=2)
        source.finish()
        frames = decode(bytes(idle) + collect(source, size=997))
        self.assertEqual([frame.packet.payload for frame in frames], [PAYLOAD_A, PAYLOAD_B, PAYLOAD_B])
        self.assertEqual([frame.packet.sync_word for frame in frames], [b"TEST", b"NEXT", b"NEXT"])
        self.assertEqual(source.snapshot()["bursts_generated"], 3)
        self.assertEqual(source.snapshot()["queue_remaining"], 0)
        self.assertEqual(source.readinto(memoryview(idle)), 0)

    def test_all_cr_modes_and_max_payload_roundtrip(self):
        for cr, bt in (("1/2", 0.5), ("3/4", 1), ("1", 0)):
            config = replace(MODULATION, coding_rate=cr, bt=bt)
            source = PacketSource(config, b"TEST")
            payload = bytes(range(127))
            source.submit(payload, repeat=2)
            source.finish()
            with self.subTest(cr=cr, bt=bt):
                self.assertEqual([frame.packet.payload for frame in decode(collect(source), config)], [payload] * 2)

    def test_full_queue_timeout_and_finish_wakes_blocked_submitter(self):
        source = PacketSource(MODULATION, b"TEST", depth=1)
        source.submit(PAYLOAD_A)
        with self.assertRaises(TimeoutError):
            source.submit(PAYLOAD_B, timeout=0)
        errors = []
        def submit():
            try:
                source.submit(PAYLOAD_B)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=submit)
        worker.start()
        source.finish()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(source.snapshot()["queue_max"], 1)

    def test_abort_stops_large_repeat_and_rejects_new_payloads(self):
        source = PacketSource(MODULATION, b"TEST")
        source.submit(PAYLOAD_A, repeat=1_000_000)
        source.readinto(memoryview(bytearray(20_000)))
        source.abort()
        self.assertLess(len(collect(source)), 20_000)
        self.assertLess(source.snapshot()["bursts_generated"], 10)
        with self.assertRaises(StreamCancelled):
            source.submit(PAYLOAD_B)

    def test_invalid_payload_sync_and_repeat_rejected(self):
        source = PacketSource(MODULATION, b"TEST")
        for payload, kwargs in ((b"bad", {}), (PAYLOAD_A, {"sync_word": b"bad"}),
                                (PAYLOAD_A, {"repeat": 0}), (PAYLOAD_A, {"repeat": True})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                source.submit(payload, **kwargs)
        with self.assertRaises(ValueError):
            PacketSource(MODULATION).submit(PAYLOAD_A)


class LiveTransmitTests(unittest.TestCase):
    def make_sender(self, api, **kwargs):
        return LiveTransmitter(MODULATION, HARDWARE, b"TEST", device_factory=lambda: HackRFDevice(api), **kwargs)

    def test_finite_transmission_flushes_and_recovers_payloads(self):
        api = FakeAPI(buffer_size=4096, tx_interval=0.001)
        sender = self.make_sender(api)
        sender.submit(PAYLOAD_A, repeat=3)
        sender.finish()
        with sender:
            stats = sender.wait()
        self.assertEqual(stats.status, "completed")
        self.assertTrue(stats.stream["flush_complete"])
        self.assertEqual(stats.stream["source_bytes"], 2 * stats.source["total_samples"])
        self.assertEqual([frame.packet.payload for frame in decode(api.sent)], [PAYLOAD_A] * 3)
        self.assertEqual(api.calls[-2:], ["close", "release"])

    def test_native_start_queues_four_blocks_before_first_completion(self):
        api = FakeAPI(buffer_size=262144, tx_interval=0.065536, tx_start_burst=4)
        sender = self.make_sender(api)
        sender.submit(PAYLOAD_A, repeat=50)
        sender.finish()
        with sender:
            sender.wait()
        self.assertEqual(sender.stats.status, "completed")
        self.assertTrue(sender.stats.stream["flush_complete"])
        self.assertGreater(sender.stats.stream["callbacks"], 4)

    def test_submit_while_transmitting_idle_then_finish(self):
        api = FakeAPI(buffer_size=4096, tx_interval=0.001)
        sender = self.make_sender(api)
        with sender:
            deadline = time.monotonic() + 2
            while len(api.sent) < 40_000:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.002)
            self.assertFalse(sender.done.is_set())
            sender.submit(PAYLOAD_A)
            sender.submit(PAYLOAD_B)
            sender.finish()
            sender.wait()
        self.assertGreater(sender.stats.source["idle_samples"], 0)
        self.assertEqual([frame.packet.payload for frame in decode(api.sent)], [PAYLOAD_A, PAYLOAD_B])

    def test_duration_limit_cancels_and_cleans_up(self):
        api = FakeAPI(buffer_size=4096, tx_interval=0.001)
        sender = self.make_sender(api, duration=0.05)
        sender.submit(PAYLOAD_A, repeat=1_000_000)
        with sender:
            sender.wait()
        self.assertEqual(sender.stats.status, "duration_reached")
        self.assertFalse(sender._thread.is_alive())
        self.assertIn("stop_tx", api.calls)
        self.assertEqual(api.calls[-1], "release")

    def test_duration_signal_at_flush_is_not_reported_completed(self):
        api = FakeAPI(buffer_size=4096)
        sender = self.make_sender(api)
        def enable_flush(handle, callback, context):
            def flush(*args):
                # 确定性重现：时限到达与最后一次 flush 完成通知同时发生。
                sender._duration_reached.set()
                sender.stop()
                callback(*args)
            api.flush = flush
            return 0
        api.hackrf_enable_tx_flush = enable_flush
        sender.submit(PAYLOAD_A)
        sender.finish()
        with sender:
            sender.wait()
        self.assertEqual(sender.stats.status, "duration_reached")
        self.assertTrue(sender.stats.stream["flush_complete"])
        self.assertEqual(api.calls[-1], "release")

    def test_native_failure_and_firmware_shortfall_propagate(self):
        for firmware in (False, True):
            api = FakeAPI(fail_start=not firmware, buffer_size=4096)
            device = HackRFDevice(api)
            if firmware:
                device.state = lambda: {"num_shortfalls": 2, "error": 0}
            sender = LiveTransmitter(MODULATION, HARDWARE, b"TEST", device_factory=lambda: device,
                                     health_interval=0.01)
            with self.subTest(firmware=firmware), self.assertRaises(Exception), sender:
                sender.wait()
            self.assertEqual(sender.stats.status, "failed")
            self.assertEqual(api.calls[-1], "release")
            if firmware:
                self.assertIsInstance(sender.error, StreamUnderrun)
            with self.assertRaises(StreamCancelled):
                sender.submit(PAYLOAD_A)


class TransmitCLITests(unittest.TestCase):
    def test_effective_configuration_is_printed_once_before_device_opens(self):
        cases = [
            ([], ["实际载频=2473720000 Hz", "采样率=2000000 Sps", "bitrate=325000 bit/s",
                  "FLRC BW=300000 Hz", "CR=3/4", "BT=1", "RampTime=12 us", "24 采样",
                  "HackRF 基带 BW=1750000 Hz", "重复=1 次"]),
            (["--bit-rate", "650000", "--offset-hz", "0", "--cr", "1/2", "--bt", "0",
              "--ramp-time-us", "20", "--tx-gain", "7", "--amplitude", "0.5", "--amp",
              "--repeat", "0", "--duration", "0.05"],
             ["实际载频=2473660000 Hz", "采样率=4000000 Sps", "bitrate=650000 bit/s",
              "FLRC BW=600000 Hz", "CR=1/2", "BT=0（关闭高斯成形）", "RampTime=20 us",
              "80 采样", "IQ 幅度=0.5", "TX gain=7 dB", "AMP=开", "重复=持续重复 (0)"]),
        ]
        for options, expected in cases:
            api, stdout, stderr = FakeAPI(buffer_size=4096, tx_interval=0.001), io.StringIO(), io.StringIO()
            original_open = api.hackrf_open
            def open_device(output):
                # 设备尚未打开时就应能看到参数，不能等发送结束才显示。
                for value in expected:
                    self.assertIn(value, stderr.getvalue())
                return original_open(output)
            api.hackrf_open = open_device
            with self.subTest(options=options), \
                    patch("flrc_modem.transmission.session.HackRFLibrary", return_value=api), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(["--frequency", "2473660000", "--sync", "d5e2870c",
                               "--payload-hex", PAYLOAD_A.hex(), *options])
                self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue().count("发送配置（实际生效值）"), 1)
            self.assertEqual(json.loads(stdout.getvalue())["sample_rate_hz"],
                             4_000_000 if options else 2_000_000)

    def test_duration_exits_while_stdin_pipe_remains_open(self):
        # 标准输入的读线程可能仍阻塞，必须保证 CLI 能完成超时清理并退出。
        root = Path(__file__).resolve().parents[1]
        script = ("import sys; sys.path.insert(0, 'src'); sys.path.insert(0, 'tests'); "
                  "from unittest.mock import patch; from hackrf_fake import FakeAPI; "
                  "from flrc_modem.transmit_cli import main; "
                  "p=patch('flrc_modem.transmission.session.HackRFLibrary', "
                  "return_value=FakeAPI(buffer_size=4096)); p.start(); "
                  "raise SystemExit(main(['--frequency','2480000000','--sync','54455354',"
                  "'--stdin-jsonl','--duration','0.05']))")
        process = subprocess.Popen([sys.executable, "-c", script], cwd=root, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b'{"payload_hex":"000102030405"}\n')
            process.stdin.flush()
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode(errors="replace"))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def run_cli(self, args, text=""):
        api, stdout, stderr = FakeAPI(buffer_size=4096, tx_interval=0.001), io.StringIO(), io.StringIO()
        with patch("flrc_modem.transmission.session.HackRFLibrary", return_value=api), \
                patch("sys.stdin", io.StringIO(text)), redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--frequency", "2480000000", *args])
        stats = json.loads(stdout.getvalue()) if stdout.getvalue() else None
        return result, api, stats, stderr.getvalue()

    def test_payload_hex_transmits_without_writing_files(self):
        with patch.object(Path, "open", side_effect=AssertionError("不应打开 IQ 或元数据文件")):
            result, api, stats, error = self.run_cli(["--sync", "54455354", "--payload-hex", PAYLOAD_A.hex(),
                                                     "--repeat", "2", "--bt", "0"])
        self.assertEqual(result, 0, error)
        self.assertEqual(stats["sample_rate_hz"], 2_000_000)
        self.assertEqual([frame.packet.payload for frame in decode(api.sent, replace(MODULATION, bt=0))],
                         [PAYLOAD_A] * 2)

    def test_jsonl_different_payload_sync_and_repeat(self):
        text = "\n".join(json.dumps(row) for row in (
            {"payload_hex": PAYLOAD_A.hex()},
            {"payload_hex": PAYLOAD_B.hex(), "sync": "4e455854", "repeat": 2}))
        result, api, stats, error = self.run_cli(["--stdin-jsonl", "--sync", "54455354"], text)
        self.assertEqual(result, 0, error)
        frames = decode(api.sent)
        self.assertEqual([frame.packet.payload for frame in frames], [PAYLOAD_A, PAYLOAD_B, PAYLOAD_B])
        self.assertEqual(frames[-1].packet.sync_word, b"NEXT")
        self.assertEqual(stats["source"]["bursts_generated"], 3)
        self.assertEqual(error.count("发送配置（实际生效值）"), 1)
        self.assertIn("首包同步字=54455354", error)
        self.assertIn("输入=JSONL", error)

    def test_continuous_repeat_with_duration(self):
        result, api, stats, error = self.run_cli(["--payload-hex", PAYLOAD_A.hex(), "--sync", "54455354",
                                                "--repeat", "0", "--duration", "0.05"])
        self.assertEqual(result, 0, error)
        self.assertEqual(stats["status"], "duration_reached")
        self.assertIn("stop_tx", api.calls)

    def test_invalid_first_input_never_opens_device(self):
        for args, text in ((["--payload-hex", "00", "--sync", "54455354"], ""),
                           (["--payload-hex", PAYLOAD_A.hex()], ""),
                           (["--stdin-jsonl"], ""), (["--stdin-jsonl"], "{}")):
            result, api, _, error = self.run_cli(args, text)
            with self.subTest(args=args, text=text):
                self.assertEqual(result, 2)
                self.assertTrue(error)
                self.assertNotIn("open", api.calls)

    def test_invalid_later_input_stops_device_and_returns_error(self):
        text = json.dumps({"payload_hex": PAYLOAD_A.hex()}) + "\nnot JSON\n"
        result, api, stats, error = self.run_cli(["--stdin-jsonl", "--sync", "54455354"], text)
        self.assertEqual(result, 2)
        self.assertIn("第 2 行", error)
        self.assertEqual(stats["status"], "failed")
        self.assertEqual(api.calls[-1], "release")

    def test_jsonl_rejects_oversize_unknown_fields_and_invalid_repeat(self):
        for text in ("x" * 4097, "[]", '{"payload_hex":"000102030405","repeta":2}',
                     '{"payload_hex":"000102030405","repeat":true}',
                     '{"payload_hex":"000102030405","sync":null}'):
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                list(iter_json_records(io.StringIO(text), sync_word=b"TEST"))
