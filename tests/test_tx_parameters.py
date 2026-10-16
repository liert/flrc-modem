"""SX1280 码率/带宽配对及 RampTime 的包络、长度和端到端检查。"""
import contextlib
import io
import json
from math import ceil
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flrc_demod.config import DemodConfig
from flrc_demod.io.iq import write_iq
from flrc_demod.modulate_cli import main, verify_signal
from flrc_demod.modulation import ModulationConfig, iter_signal, modulate_bits
from flrc_demod.phy.encoder import encode_packet
from flrc_demod.phy.profile import FLRC_RATE_PROFILES, RAMP_TIME_PARAMS
from flrc_demod.pipeline import iter_frames


class TxParameterTests(unittest.TestCase):
    def test_sx1280_rate_bandwidth_pairs(self):
        # 独立列出手册数值，避免测试只复述注册表本身。
        expected = ((260000, 300000, 0xEB), (325000, 300000, 0xC7),
                    (520000, 600000, 0xAA), (650000, 600000, 0x86),
                    (1040000, 1200000, 0x69), (1300000, 1200000, 0x45))
        self.assertEqual(len(FLRC_RATE_PROFILES), 6)
        for rate, bandwidth, parameter in expected:
            with self.subTest(rate=rate):
                self.assertEqual(FLRC_RATE_PROFILES[rate], (bandwidth, parameter))
                self.assertEqual(ModulationConfig(bit_rate=rate).bandwidth_hz, bandwidth)
                self.assertEqual(ModulationConfig(bit_rate=rate, bandwidth_hz=bandwidth).bandwidth_hz, bandwidth)

    def test_invalid_rate_bandwidth_and_ramp_are_rejected(self):
        for values in ({"bit_rate": 300000}, {"bandwidth_hz": 600000},
                       {"bandwidth_hz": float("nan")}, {"bandwidth_hz": -1},
                       {"ramp_time_us": 0}, {"ramp_time_us": 3},
                       {"ramp_time_us": float("inf")}, {"ramp_time_us": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ModulationConfig(**values)

    def test_ramp_envelope_is_monotonic_and_outside_data(self):
        bits = np.tile([0, 1], 60)
        for microseconds in (2, 4, 6, 8, 10, 12, 16, 20):
            with self.subTest(ramp=microseconds):
                config = ModulationConfig(ramp_time_us=microseconds)
                waveform = modulate_bits(bits, config)
                count = config.ramp_samples
                amplitude = np.abs(waveform)
                expected = np.arange(count) / count * config.amplitude
                np.testing.assert_allclose(amplitude[:count], expected, atol=1e-7)
                np.testing.assert_allclose(amplitude[-count:], expected[::-1], atol=1e-7)
                np.testing.assert_allclose(amplitude[count:-count], config.amplitude, atol=1e-7)
                self.assertEqual(count, microseconds * 10)
                self.assertEqual(len(waveform), ceil((len(bits) + 8) * config.sample_rate / config.bit_rate) + 2 * count)

    def test_ramp_duration_uses_nearest_sampling_interval(self):
        config = ModulationConfig(sample_rate=2_123_456, ramp_time_us=12)
        actual_us = config.ramp_samples / config.sample_rate * 1_000_000
        self.assertLessEqual(abs(actual_us - 12), 0.5 * 1_000_000 / config.sample_rate)

    def test_all_six_rates_and_eight_ramps_iq_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.iq"
            for rate in FLRC_RATE_PROFILES:
                for ramp in RAMP_TIME_PARAMS:
                    for size in (6, 127):
                        with self.subTest(rate=rate, ramp=ramp, size=size):
                            config = ModulationConfig(bit_rate=rate, ramp_time_us=ramp, repeat=2)
                            packet = encode_packet(bytes(range(size)), b"TEST")
                            write_iq(path, iter_signal(packet.bits, config), "cs8")
                            self.assertEqual(verify_signal(path, config, packet), 2)

    def test_preamble_time_includes_ramp_lead_in(self):
        config = ModulationConfig(bit_rate=1_300_000, ramp_time_us=20)
        packet = encode_packet(bytes(range(32)), b"TEST")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.iq"
            write_iq(path, iter_signal(packet.bits, config), "cs8")
            demod = DemodConfig(bit_rate=config.bit_rate, filter_cutoff=1_400_000)
            frames = list(iter_frames(path, demod))
            self.assertEqual(len(frames), 1)
            self.assertAlmostEqual(frames[0].time_s, config.guard_seconds + config.ramp_samples / config.sample_rate,
                                   delta=2 / config.bit_rate)

    def test_cli_metadata_records_selected_and_effective_parameters(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / "signal.iq"
            self.assertEqual(main(["--payload-hex", "0011223344556677", "--sync", "b42b2bb4", "--bit-rate", "650000",
                                   "--bandwidth-hz", "600000", "--ramp-time-us", "20", "--output", str(path), "--verify"]), 0)
            metadata = json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["config"]["bandwidth_hz"], 600000)
            self.assertEqual(metadata["config"]["ramp_time_us"], 20)
            self.assertEqual(metadata["ramp_samples"], 200)
            self.assertEqual(metadata["preamble_offset_samples"], 200)
            self.assertEqual(metadata["effective_ramp_time_us"], 20)
            self.assertEqual(metadata["sx1280_bit_rate_bandwidth_param"], "0x86")
            self.assertEqual(metadata["sx1280_ramp_time_param"], "0xe0")

    def test_cli_pair_mismatch_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / "signal.iq"
            path.write_bytes(b"original")
            self.assertEqual(main(["--payload-hex", "0011223344556677", "--sync", "b42b2bb4", "--bit-rate", "325000",
                                   "--bandwidth", "600000", "--output", str(path)]), 2)
            self.assertEqual(path.read_bytes(), b"original")
