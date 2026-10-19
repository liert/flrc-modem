"""三种 CR 与三种 BT 的编解码、信号回环和错误配置检查。"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flrc_modem.cli import main as demod_main
from flrc_modem.config import DemodConfig, load_config
from flrc_modem.dsp.shaping import preamble_contrast, shape_frequency
from flrc_modem.io.iq import write_iq
from flrc_modem.modulate_cli import main as modulate_main, verify_signal
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.phy.fec import coded_length, convolutional_decode, convolutional_encode
from flrc_modem.phy.packet import decode_packet
from flrc_modem.phy.profile import CODING_RATES, parse_bt, tail_length
from flrc_modem.phy.sync import PREFIX_BITS


class ProfileTests(unittest.TestCase):
    def test_fec_impulse_golden_vectors(self):
        # 单位冲激的七个移位寄存器值为 1、2、4、8、16、32、64。
        # 逐位计算 0x6D/0x4F 的奇偶校验，独立于生产编码器。
        bits = np.array([1, 0, 0, 0, 0, 0, 0], dtype=np.uint8)
        expected = {"1/2": "11011111001011", "3/4": "1101110011", "1": "1000000"}
        for cr in CODING_RATES:
            with self.subTest(cr=cr):
                encoded = convolutional_encode(bits, cr)
                self.assertEqual("".join(str(bit) for bit in encoded), expected[cr])
                decoded, metric = convolutional_decode(encoded, len(bits), terminated=cr != "1", coding_rate=cr)
                np.testing.assert_array_equal(decoded, bits)
                self.assertEqual(metric, 0)

    def test_half_rate_corrects_bit_error(self):
        bits = np.concatenate((np.unpackbits(np.frombuffer(b"coding test", dtype=np.uint8)), np.zeros(6, dtype=np.uint8)))
        coded = convolutional_encode(bits, "1/2")
        coded[55] ^= 1
        decoded, metric = convolutional_decode(coded, len(bits), True, "1/2")
        np.testing.assert_array_equal(decoded, bits)
        self.assertEqual(metric, 1)

    def test_packet_length_tail_and_crc_for_all_rates(self):
        for cr, expected_maximum in (("1/2", 2124), ("3/4", 1416), ("1", 1056)):
            with self.subTest(cr=cr):
                self.assertEqual(coded_length(16 + 8 * 127 + 24 + tail_length(cr), cr), expected_maximum)
                packet = encode_packet(bytes(range(127)), b"TEST", coding_rate=cr)
                self.assertEqual(len(packet.bits), PREFIX_BITS + expected_maximum)
                result = decode_packet(packet.bits[PREFIX_BITS:], (b"TEST",), coding_rate=cr)
                self.assertEqual(result.payload, bytes(range(127)))
                self.assertIsNone(decode_packet(packet.bits[PREFIX_BITS:-1], (b"TEST",), coding_rate=cr))

    def test_wrong_rate_does_not_emit_a_packet(self):
        for actual in CODING_RATES:
            packet = encode_packet(bytes(range(32)), b"TEST", coding_rate=actual)
            for wrong in set(CODING_RATES) - {actual}:
                with self.subTest(actual=actual, wrong=wrong):
                    self.assertIsNone(decode_packet(packet.bits[PREFIX_BITS:], (b"TEST",), coding_rate=wrong))

    def test_bt_off_bypasses_filter_and_changes_preamble_contrast(self):
        frequency = np.repeat([-1.0, 1.0, -1.0, 1.0], 16)
        np.testing.assert_array_equal(shape_frequency(frequency, 16, 0), frequency)
        self.assertFalse(np.array_equal(shape_frequency(frequency, 16, 0.5), frequency))
        self.assertLess(preamble_contrast(0.5), preamble_contrast(1))
        self.assertLess(preamble_contrast(1), preamble_contrast(0))
        self.assertEqual(parse_bt("0"), 0)
        self.assertEqual(parse_bt(0), 0)

    def test_dynamic_window_and_overlap_cover_half_rate_maximum(self):
        config = DemodConfig(coding_rate="1/2")
        self.assertGreaterEqual(config.max_coded_bits, 2124)
        self.assertGreater(config.overlap_seconds, (85 + 2124) / config.bit_rate)
        for values in ({"max_coded_bits": 1500}, {"overlap_seconds": 0.006}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DemodConfig(coding_rate="1/2", **values)

    def test_invalid_modes_and_toml_override(self):
        for kind in (DemodConfig, ModulationConfig):
            for values in ({"coding_rate": "2/3"}, {"bt": "auto"}, {"bt": "no"},
                           {"bt": "OFF"}, {"bt": -1}, {"bt": 0.7}):
                with self.subTest(kind=kind, values=values), self.assertRaises(ValueError):
                    kind(**values)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.toml"
            path.write_text('[demod]\ncoding_rate="1/2"\nbt=0\n', encoding="utf-8")
            self.assertEqual(load_config(path, {}).bt, 0)
            config = load_config(path, {"coding_rate": None, "bt": 0.5})
            self.assertEqual(config.coding_rate, "1/2")
            self.assertEqual(config.bt, 0.5)

    def test_all_nine_combinations_iq_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signal.iq"
            for cr in CODING_RATES:
                for bt in (0, 0.5, 1):
                    for size in (6, 34, 127):
                        with self.subTest(cr=cr, bt=bt, size=size):
                            config = ModulationConfig(coding_rate=cr, bt=bt, sample_rate=2_000_000, repeat=2)
                            packet = encode_packet(bytes(range(size)), b"TEST", coding_rate=cr)
                            write_iq(path, iter_signal(packet.bits, config), "cs8")
                            self.assertEqual(verify_signal(path, config, packet), 2)

    def test_cli_profile_roundtrip_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            path = Path(directory) / "signal.iq"
            result = Path(directory) / "frames.jsonl"
            self.assertEqual(modulate_main(["--payload-hex", "0011223344556677", "--sync", "b42b2bb4",
                                           "--cr", "1/2", "--bt", "0", "--output", str(path), "--verify"]), 0)
            metadata = json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["config"]["coding_rate"], "1/2")
            self.assertEqual(metadata["config"]["bt"], 0)
            self.assertEqual(demod_main(["--iq", str(path), "--cr", "1/2", "--bt", "0",
                                        "--output", str(result), "--quiet"]), 0)
            records = result.read_text().splitlines()
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0])["payload_hex"], "0011223344556677")
