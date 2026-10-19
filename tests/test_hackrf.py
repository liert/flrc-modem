"""独立后端的尾包、队列、取消、错误回收、IQ 格式及 DLL 清单回归。"""
import ctypes as C
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from flrc_modem.io.iq import encode_samples
from flrc_modem.sdr.hackrf import (HackRFConfig, HackRFDevice, HackRFError, HackRFLibrary,
                                  IQFileSource, IQIterableSource, StreamCancelled,
                                  StreamOverrun, StreamUnderrun, bundled_dll_path)
from flrc_modem.sdr.hackrf.abi import M0State, Transfer
from flrc_modem.sdr.hackrf.cli import main as hackrf_main
from flrc_modem.sdr.hackrf.streaming import RxStream, TxStream
from hackrf_fake import FakeAPI, transfer


class HackRFTests(unittest.TestCase):
    def test_callback_abi_layout(self):
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(Transfer), 40)
            self.assertEqual(Transfer.valid_length.offset, 20)
            self.assertEqual(Transfer.tx_ctx.offset, 32)

    def test_bundled_dll_manifest_hashes(self):
        root = bundled_dll_path().parent
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(manifest["files"]), 3)
        for entry in manifest["files"]:
            data = (root / entry["name"]).read_bytes()
            self.assertEqual(len(data), entry["size_bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
        self.assertTrue((root / "LICENSE-libhackrf.txt").is_file())
        self.assertTrue((root / "LICENSE-libusb.txt").is_file())
        self.assertTrue((root / "LICENSE-winpthread.txt").is_file())

    @unittest.skipUnless(os.name == "nt" and C.sizeof(C.c_void_p) == 8, "Windows x64 DLL")
    def test_bundled_dll_loads_without_opening_device(self):
        api = HackRFLibrary()
        self.assertEqual(api.hackrf_library_version(), b"0.9.2")
        self.assertEqual(api.hackrf_library_release(), b"2026.01.3")
        self.assertEqual(api.hackrf_get_transfer_buffer_size(None), 262144)

    def test_hardware_parameter_validation(self):
        self.assertEqual(HackRFConfig(2480000000).bandwidth_hz, 7000000)
        for values in ({"frequency_hz": 0}, {"sample_rate": float("nan")},
                       {"sample_rate": 1e6}, {"bandwidth_hz": 300000},
                       {"tx_gain": 48}, {"lna_gain": 9}, {"vga_gain": 3}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                HackRFConfig(**{"frequency_hz": 2480000000, **values})

    def test_tx_tail_is_submitted_then_flushed_before_stop(self):
        for size in (2, 510, 512, 514, 3000, 8192):
            with self.subTest(size=size):
                data = (bytes(range(256)) * 32)[:size]
                api = FakeAPI()
                stream = TxStream(api, C.c_void_p(42), io.BytesIO(data), timeout=2)
                stats = stream.run()
                self.assertEqual(bytes(api.sent[:size]), data)
                self.assertTrue(all(value == 0 for value in api.sent[size:]))
                self.assertEqual(len(api.sent), (size + 511) // 512 * 512)
                self.assertEqual(stats.source_bytes, size)
                self.assertEqual(stats.completed_bytes, stats.submitted_bytes)
                self.assertTrue(stats.flush_complete)
                self.assertLess(api.calls.index("flush"), api.calls.index("stop_tx"))
                self.assertTrue(stream.stopped)

    def test_tx_pool_supports_multiple_producer_block_sizes(self):
        data = bytes(range(256)) * 24
        for size in (512, 1024, 2048):
            with self.subTest(block_bytes=size):
                api = FakeAPI()
                stats = TxStream(api, C.c_void_p(42), io.BytesIO(data), block_bytes=size, timeout=2).run()
                self.assertEqual(api.sent, data)
                self.assertEqual(stats.source_bytes, len(data))

    def test_tx_short_reads_and_odd_source_rejection(self):
        class ShortSource(io.BytesIO):
            def readinto(self, destination):
                return super().readinto(destination[:7])
        api = FakeAPI()
        TxStream(api, C.c_void_p(42), ShortSource(b"a" * 514), timeout=2).run()
        self.assertEqual(api.sent[:514], b"a" * 514)
        for data in (b"", b"odd"):
            api = FakeAPI()
            with self.subTest(data=data), self.assertRaises(ValueError):
                TxStream(api, C.c_void_p(42), io.BytesIO(data)).run()
            self.assertNotIn("start_tx", api.calls)

    def test_tx_native_errors_stop_and_are_not_reported_as_success(self):
        for failure in ("fail_start", "fail_flush", "fail_complete"):
            api = FakeAPI(**{failure: True})
            with self.subTest(failure=failure), self.assertRaises(HackRFError):
                TxStream(api, C.c_void_p(42), io.BytesIO(b"a" * 512), timeout=2).run()
            self.assertIn("stop_tx", api.calls)

    def test_tx_underrun_policy_is_explicit(self):
        storage, block = transfer()
        for policy in ("stop", "zeros"):
            stream = TxStream(FakeAPI(), C.c_void_p(42), io.BytesIO(), underrun_policy=policy)
            result = stream.callback(C.byref(block))
            if policy == "stop":
                self.assertEqual(result, 1)
                self.assertIsInstance(stream.error, StreamUnderrun)
            else:
                self.assertEqual(result, 0)
                self.assertEqual(block.valid_length, 512)
                self.assertEqual(stream.stats.zero_fill_bytes, 512)
                self.assertEqual(C.string_at(block.buffer, 512), bytes(512))

    def test_cancelled_tx_does_not_start_native_stream(self):
        api, cancel = FakeAPI(), threading.Event()
        cancel.set()
        with self.assertRaises(StreamCancelled):
            TxStream(api, C.c_void_p(42), io.BytesIO(b"a" * 512), cancel_event=cancel).run()
        self.assertNotIn("start_tx", api.calls)

    def test_rx_exact_count_and_partial_sink_writes(self):
        class ShortSink(io.BytesIO):
            def write(self, data):
                return super().write(data[:31])
        data = bytes(range(256)) * 8
        sink, api = ShortSink(), FakeAPI(rx_data=data)
        stream = RxStream(api, C.c_void_p(42), sink, sample_count=750, timeout=2)
        stats = stream.run()
        self.assertEqual(sink.getvalue(), data[:1500])
        self.assertEqual(stats.source_bytes, 1500)
        self.assertEqual(stats.written_bytes, 1500)
        self.assertIn("stop_rx", api.calls)

    def test_rx_overrun_is_reported_without_silent_drops(self):
        stream = RxStream(FakeAPI(), C.c_void_p(42), io.BytesIO(), sample_count=5000, queue_depth=2)
        storage, block = transfer(b"a" * 512)
        self.assertEqual(stream.callback(C.byref(block)), 0)
        self.assertEqual(stream.callback(C.byref(block)), 0)
        self.assertEqual(stream.callback(C.byref(block)), 1)
        self.assertIsInstance(stream.error, StreamOverrun)

    def test_rx_sink_failure_and_timeout_stop_native_stream(self):
        class BrokenSink:
            def write(self, data):
                raise OSError("disk failed")
        api = FakeAPI(rx_data=b"a" * 512)
        with self.assertRaisesRegex(OSError, "disk failed"):
            RxStream(api, C.c_void_p(42), BrokenSink(), sample_count=256, timeout=2).run()
        self.assertIn("stop_rx", api.calls)
        api = FakeAPI()
        with self.assertRaises(TimeoutError):
            RxStream(api, C.c_void_p(42), io.BytesIO(), sample_count=1, timeout=0.1).run()
        self.assertIn("stop_rx", api.calls)

    def test_stream_options_reject_unbounded_or_invalid_buffers(self):
        for options in ({"queue_depth": 1}, {"block_bytes": 513}, {"timeout": 0},
                        {"prefill_blocks": 9}, {"underrun_policy": "ignore"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                TxStream(FakeAPI(), None, io.BytesIO(), **options)

    def test_file_formats_and_repeat_conversion_run_outside_callback(self):
        samples = np.array([0.25 + 0.5j, -0.25 - 0.5j], dtype=np.complex64)
        expected = encode_samples(samples, "cs8") * 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.iq"
            for iq_format in ("cs8", "cu8", "cf32"):
                with self.subTest(format=iq_format):
                    path.write_bytes(encode_samples(samples, iq_format))
                    with IQFileSource(path, iq_format, repeat=3) as source:
                        destination = bytearray(512)
                        count = source.readinto(memoryview(destination))
                        self.assertEqual(count, len(expected))
                        self.assertEqual(destination[:count], expected)
                        self.assertEqual(source.readinto(memoryview(destination)), 0)

    def test_complex_iterable_source_and_empty_chunks(self):
        samples = np.array([0.25 + 0.5j, -0.25 - 0.5j], dtype=np.complex64)
        source = IQIterableSource([np.array([], dtype=np.complex64), samples, samples])
        destination = bytearray(6)
        first = source.readinto(memoryview(destination))
        output = bytes(destination[:first])
        second = source.readinto(memoryview(destination))
        output += bytes(destination[:second])
        self.assertEqual(output, encode_samples(np.tile(samples, 2), "cs8"))

    def test_device_lifecycle_configures_before_stream_and_releases(self):
        api = FakeAPI()
        with HackRFDevice(api, serial="abcd") as device:
            stats = device.transmit(io.BytesIO(b"a" * 512), HackRFConfig(2480000000), timeout=2)
            self.assertTrue(stats.flush_complete)
            self.assertLess(api.calls.index(("hackrf_set_freq", 2480000000)), api.calls.index("start_tx"))
            self.assertEqual(device.selected_device["index"], 0)
            self.assertTrue(device.selected_device["serial"].endswith("abcd"))
            with self.assertRaises(HackRFError):
                device.__enter__()
        self.assertEqual(api.calls[-2:], ["close", "release"])

    def test_failed_native_stop_retains_callbacks_until_device_close(self):
        api = FakeAPI()
        original = api.hackrf_stop_tx
        def failed_stop(handle):
            original(handle)
            return -1
        api.hackrf_stop_tx = failed_stop
        with HackRFDevice(api) as device:
            with self.assertRaises(HackRFError):
                device.transmit(io.BytesIO(b"a" * 512), HackRFConfig(2480000000), timeout=2)
            self.assertIsNotNone(device._stream)
            self.assertIsNotNone(device._stream.callback)
            with self.assertRaisesRegex(HackRFError, "上一次"):
                device.transmit(io.BytesIO(b"a" * 512), HackRFConfig(2480000000))

    def test_tx_rx_reuse_clears_native_tx_callbacks(self):
        api = FakeAPI(rx_data=b"r" * 512)
        with HackRFDevice(api) as device:
            config = HackRFConfig(2480000000)
            device.transmit(io.BytesIO(b"t" * 512), config, timeout=2)
            self.assertFalse(api.complete)
            self.assertIsNone(api.flush)
            sink = io.BytesIO()
            device.receive(sink, config, sample_count=256, timeout=2)
            self.assertEqual(sink.getvalue(), b"r" * 512)
            device.transmit(io.BytesIO(b"u" * 512), config, timeout=2)

    def test_close_error_does_not_retry_freed_handle(self):
        api = FakeAPI()
        def bad_close(handle):
            api.calls.append("close")
            return -1
        api.hackrf_close = bad_close
        device = HackRFDevice(api).__enter__()
        with self.assertRaises(HackRFError):
            device.close()
        self.assertIsNone(device.handle.value)
        device.close()
        self.assertEqual(api.calls.count("close"), 1)

    def test_firmware_shortfalls_fail_by_default_and_can_be_reported(self):
        for mode in ("tx", "rx"):
            for allow in (False, True):
                with self.subTest(mode=mode, allow=allow):
                    api = FakeAPI(rx_data=b"a" * 512)
                    def state(handle, output):
                        C.cast(output, C.POINTER(M0State)).contents.num_shortfalls = 3
                        return 0
                    api.hackrf_get_m0_state = state
                    with HackRFDevice(api) as device:
                        def run():
                            config = HackRFConfig(2480000000)
                            if mode == "tx":
                                return device.transmit(io.BytesIO(b"a" * 512), config, allow_device_shortfalls=allow, timeout=2)
                            return device.receive(io.BytesIO(), config, 256, allow_device_shortfalls=allow, timeout=2)
                        if allow:
                            self.assertEqual(run().device_state["num_shortfalls"], 3)
                        else:
                            with self.assertRaises(StreamUnderrun if mode == "tx" else StreamOverrun):
                                run()
                        self.assertEqual(device.last_stats.device_state["num_shortfalls"], 3)

    def test_hackrf_cli_tx_and_rx_use_independent_backend(self):
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.cs8", Path(directory) / "output.cs8"
            source.write_bytes(b"a" * 514)
            for mode in ("tx", "rx"):
                api = FakeAPI(rx_data=b"b" * 512)
                args = [mode, "--frequency", "2480000000"]
                args += ["--iq", str(source)] if mode == "tx" else ["--output", str(output), "--duration", "0.0000256"]
                stdout = io.StringIO()
                with patch("flrc_modem.sdr.hackrf.cli.HackRFLibrary", return_value=api), redirect_stdout(stdout):
                    self.assertEqual(hackrf_main(args), 0)
                record = json.loads(stdout.getvalue())
                self.assertEqual(record["mode"], mode)
            self.assertEqual(output.read_bytes(), b"b" * 512)
