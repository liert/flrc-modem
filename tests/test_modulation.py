"""捕获金标准、IQ 落盘回读及写入失败边界，验证完整调制链路。"""
import contextlib
import io
import json
from math import ceil
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flrc_demod.config import DemodConfig
from flrc_demod.io.iq import decode_samples, encode_samples, write_iq
from flrc_demod.modulate_cli import main, verify_signal
from flrc_demod.modulation import ModulationConfig, iter_signal, modulate_bits
from flrc_demod.phy.encoder import encode_packet
from flrc_demod.phy.fec import convolutional_encode
from flrc_demod.phy.sync import PREFIX_BITS, recover_coded_bits
from flrc_demod.pipeline import iter_frames

FIXTURE = json.loads((Path(__file__).parent / "fixtures/dk32s_packet.json").read_text())


class ModulationTests(unittest.TestCase):
    def test_encoder_agrees_with_captured_packet(self):
        packet = encode_packet(bytes.fromhex(FIXTURE["payload_hex"]), bytes.fromhex(FIXTURE["sync_hex"]))
        self.assertEqual(packet.header.hex(), FIXTURE["header_hex"])
        self.assertEqual(packet.crc.hex(), FIXTURE["crc_hex"])
        captured = np.unpackbits(np.frombuffer(bytes.fromhex(FIXTURE["coded_hex"]), dtype=np.uint8))
        expected = recover_coded_bits(packet.bits[PREFIX_BITS:], 0x66)
        # 实际捕获存在一个 FEC 可纠正错误；不要求干净编码器复制噪声。
        errors = [np.count_nonzero(recover_coded_bits(captured[:len(expected)], mask) != expected)
                  for mask in (0x66, 0x99)]
        self.assertEqual(min(errors), 1)

    def test_packet_input_boundaries(self):
        for size in (0, 5, 128):
            with self.subTest(size=size), self.assertRaises(ValueError):
                encode_packet(b"a" * size, b"TEST")
        for sync, seed in ((b"bad", 0), (b"TEST", -1), (b"TEST", 0x1000000)):
            with self.subTest(sync=sync, seed=seed), self.assertRaises(ValueError):
                encode_packet(b"a" * 6, sync, seed)

    def test_bit_input_rejected_before_modulation(self):
        for bits in ([], [0, 2], [[0, 1]], [float("nan")]):
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                modulate_bits(bits, ModulationConfig())
        with self.assertRaises(ValueError):
            convolutional_encode([0, 2])

    def test_configuration_boundaries(self):
        for overrides in (
            {"sample_rate": float("nan")}, {"bit_rate": 0}, {"sample_rate": 1_000_000},
            {"bt": 0}, {"amplitude": 1.1}, {"offset_hz": 5_000_000},
            {"repeat": 0}, {"repeat": 1.5}, {"gap_seconds": -1},
            {"guard_seconds": float("inf")}, {"iq_format": "invalid"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                ModulationConfig(**overrides)

    def test_constant_bits_have_expected_frequency_deviation(self):
        config = ModulationConfig(sample_rate=2_000_000)
        signal = modulate_bits(np.ones(200, dtype=np.uint8), config)
        instantaneous = np.angle(signal[1:] * signal[:-1].conj()) * config.sample_rate / (2 * np.pi)
        np.testing.assert_allclose(instantaneous[100:-100], config.deviation_hz, atol=0.1)
        self.assertLessEqual(float(np.abs(signal).max()), config.amplitude + 1e-6)

    def test_sample_count_and_silence(self):
        config = ModulationConfig(sample_rate=2_000_000, repeat=3)
        packet = encode_packet(bytes(range(12)), b"TEST")
        chunks = list(iter_signal(packet.bits, config))
        count = sum(len(chunk) for chunk in chunks)
        burst = ceil((len(packet.bits) + 8) * config.sample_rate / config.bit_rate) + 2 * config.ramp_samples
        self.assertEqual(count, 3 * burst + round(2 * config.guard_seconds * config.sample_rate)
                         + 2 * round(config.gap_seconds * config.sample_rate))
        self.assertTrue(np.all(chunks[0] == 0))
        self.assertTrue(np.all(chunks[-1] == 0))

    def test_integer_iq_encoding_layout_and_saturation(self):
        iq = np.array([0 + 0.5j, -1 + 1j, 2 - 2j])
        self.assertEqual(encode_samples(iq, "cs8"), bytes([0, 64, 128, 127, 127, 128]))
        self.assertEqual(encode_samples(iq, "cu8"), bytes([128, 192, 0, 255, 255, 0]))
        np.testing.assert_array_equal(decode_samples(encode_samples(iq, "cf32"), "cf32"), iq)

    def test_writer_does_not_destroy_existing_file_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.iq"
            path.write_bytes(b"original")
            with self.assertRaises(ValueError):
                write_iq(path, [np.array([0j]), np.array([complex("nan")])], "cs8")
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_all_formats_and_payload_limits_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.iq"
            for iq_format in ("cs8", "cu8", "cf32"):
                for size, sample_rate in ((6, 2_000_000), (127, 10_000_000)):
                    with self.subTest(format=iq_format, size=size, rate=sample_rate):
                        packet = encode_packet(bytes(range(size)), b"TEST")
                        config = ModulationConfig(sample_rate=sample_rate, iq_format=iq_format, offset_hz=-60_000)
                        write_iq(path, iter_signal(packet.bits, config), iq_format)
                        self.assertEqual(verify_signal(path, config, packet), 1)

    def test_repeats_across_read_windows_are_not_lost_or_merged(self):
        packet = encode_packet(bytes.fromhex(FIXTURE["payload_hex"]), bytes.fromhex(FIXTURE["sync_hex"]))
        config = ModulationConfig(sample_rate=2_000_000, repeat=12)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.iq"
            write_iq(path, iter_signal(packet.bits, config), config.iq_format)
            self.assertEqual(verify_signal(path, config, packet), 12)
            frames = list(iter_frames(path, DemodConfig(sample_rate=config.sample_rate)))
            self.assertTrue(all(frame.packet.fec_path_metric == 0 for frame in frames))

    def test_cli_payload_file_metadata_and_custom_crc_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            payload = path / "payload.bin"
            payload.write_bytes(bytes(range(16)))
            output = path / "signal.iq"
            with contextlib.redirect_stdout(io.StringIO()):
                result = main(["--payload-file", str(payload), "--sync", "12345678",
                               "--crc-initial", "0x123456", "--output", str(output), "--verify"])
            self.assertEqual(result, 0)
            metadata = json.loads(Path(str(output) + ".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["payload_hex"], payload.read_bytes().hex())
            self.assertEqual(metadata["sample_count"] * 2, output.stat().st_size)
            self.assertEqual(metadata["crc_initial"], 0x123456)

    def test_cli_rejects_input_and_metadata_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"original")
            for output, metadata in ((path, path.with_suffix(".json")),
                                     (path.with_suffix(".iq"), path),
                                     (path.with_suffix(".iq"), path.with_suffix(".iq"))):
                with self.subTest(output=output, metadata=metadata), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["--payload-file", str(path), "--sync", "54455354",
                                           "--output", str(output), "--metadata", str(metadata)]), 2)
                self.assertEqual(path.read_bytes(), b"original")

    def test_cli_verification_failure_is_reported(self):
        # 幅度在 cs8 量化后全零，不能被误报为调制/回读成功。
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = main(["--payload-hex", "000102030405", "--sync", "54455354", "--amplitude", "0.00001",
                           "--output", str(Path(directory) / "silent.iq"), "--verify"])
            self.assertEqual(result, 2)
