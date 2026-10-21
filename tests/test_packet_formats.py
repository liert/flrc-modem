"""真实 H12 比特/硬件 CRC 金标准，以及跨成包参数的解码边界。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from flrc_modem.cli import make_argument_parser
from flrc_modem.cli_options import demod_overrides
from flrc_modem.config import DemodConfig, load_config
from flrc_modem.models import DecodedFrame
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.io.iq import write_iq
from flrc_modem.io.output import format_frame
from flrc_modem.phy.crc import flrc_crc
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.phy.fec import coded_length
from flrc_modem.phy.packet import decode_packet
from flrc_modem.phy.profile import tail_length
from flrc_modem.phy.sync import PREFIX_BITS
from flrc_modem.pipeline import iter_frames

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = json.loads((Path(__file__).parent / "fixtures/h12_packets.json").read_text())


class PacketFormatTests(unittest.TestCase):
    def test_crc_check_values_from_polynomial_long_division(self):
        # 独立按 GF(2) 多项式长除生成的 123456789 向量（无反射/末尾异或）。
        for width, initial, expected in ((2, 0, 0x20FE), (2, 0xFFFF, 0x375D),
                                          (3, 0, 0xB0C390), (3, 0xFFFFFF, 0xCAE6B7),
                                          (4, 0, 0x6C9F84A8), (4, 0xFFFFFFFF, 0xC683B9E5)):
            with self.subTest(width=width, initial=initial):
                self.assertEqual(flrc_crc(b"123456789", width, initial), expected)

    def test_h12_recorded_bits_and_independent_hardware_crc(self):
        for capture in CAPTURES:
            with self.subTest(kind=capture["kind"]):
                sync, payload, crc = (bytes.fromhex(capture[key]) for key in ("sync_hex", "payload_hex", "crc_hex"))
                raw = np.unpackbits(np.frombuffer(bytes.fromhex(capture["raw_coded_hex"]), dtype=np.uint8))
                seed = capture["crc_initial"]
                self.assertEqual(flrc_crc(sync + payload, 4, seed), int.from_bytes(crc, "big"))
                result = decode_packet(raw, (sync,), seed, "1", header_type="fixed", crc_bytes=4,
                                       payload_lengths=(32, 42, 97, 127))
                self.assertIsNotNone(result)
                self.assertEqual((result.header, result.payload, result.crc), (b"", payload, crc))
                self.assertEqual(result.crc_initial, seed)
                # 老设置、错误初值、截断、损坏硬件 CRC 均不能输出。
                self.assertIsNone(decode_packet(raw, (sync,), coding_rate="1"))
                options = dict(header_type="fixed", crc_bytes=4, payload_lengths=(len(payload),))
                self.assertIsNone(decode_packet(raw, (sync,), seed ^ 0xFFFFFFFF, "1", **options))
                self.assertIsNone(decode_packet(raw[:-1], (sync,), seed, "1", **options))
                damaged = raw.copy()
                damaged[-1] ^= 1
                self.assertIsNone(decode_packet(damaged, (sync,), seed, "1", **options))

    def test_every_header_crc_and_coding_rate_with_short_and_maximum_payload(self):
        for header in ("fixed", "variable"):
            for crc_bytes in (2, 3, 4):
                for rate in ("1/2", "3/4", "1"):
                    for size in (6, 127):
                        with self.subTest(header=header, crc_bytes=crc_bytes, rate=rate, size=size):
                            payload = bytes(range(size))
                            encoded = encode_packet(payload, b"TEST", coding_rate=rate,
                                                    header_type=header, crc_bytes=crc_bytes)
                            options = dict(header_type=header, crc_bytes=crc_bytes, payload_lengths=(size,))
                            raw = encoded.bits[PREFIX_BITS:]
                            result = decode_packet(raw, (b"TEST",), coding_rate=rate, **options)
                            self.assertEqual(result.payload, payload)
                            self.assertEqual(result.header, b"" if header == "fixed" else bytes((0, size)))
                            self.assertEqual(len(result.crc), crc_bytes)
                            self.assertIsNone(decode_packet(raw[:-1], (b"TEST",), coding_rate=rate, **options))

    def test_auto_header_and_unknown_fixed_length_need_no_application_magic(self):
        for header in ("fixed", "variable"):
            payload = b"\xfe" + bytes(range(52))
            encoded = encode_packet(payload, b"TEST", coding_rate="1", header_type=header, crc_bytes=2)
            result = decode_packet(encoded.bits[PREFIX_BITS:], (b"TEST",), coding_rate="1",
                                   header_type="auto", crc_bytes=2)
            self.assertEqual(result.payload, payload)

    def test_crc32_blind_sync_and_seed_ambiguity_is_reported(self):
        capture = CAPTURES[-1]
        sync = bytes.fromhex(capture["sync_hex"])
        inverted = bytes(byte ^ 255 for byte in sync)
        raw = np.unpackbits(np.frombuffer(bytes.fromhex(capture["raw_coded_hex"]), dtype=np.uint8))
        packet = decode_packet(raw, (inverted, sync), coding_rate="1", header_type="fixed", crc_bytes=4,
                               payload_lengths=(127,), crc_initials=(0xFFFFFFFF, 0))
        self.assertEqual(set(packet.sync_crc_candidates), {(sync, 0), (inverted, 0xFFFFFFFF)})
        frame = DecodedFrame(0, 2402380000, 0, -10, 1, 0, packet)
        record = frame.to_record()
        self.assertTrue(record["sync_word_ambiguous"])
        self.assertEqual(len(record["sync_crc_candidates"]), 2)
        self.assertIn("CRC32=OK", format_frame(frame))

    def test_zero_seed_only_blind_pairing_recovers_t12s_for_ack(self):
        t12s = bytes.fromhex("54313253")
        inverse = bytes(byte ^ 255 for byte in t12s)
        for capture in CAPTURES:
            if capture["kind"] not in ("pairing_request", "pairing_ack"):
                continue
            raw = np.unpackbits(np.frombuffer(bytes.fromhex(capture["raw_coded_hex"]), dtype=np.uint8))
            # 仅初值 0；不指定唯一同步字，交给 CRC 在互补候选中裁决。
            packet = decode_packet(raw, (inverse, t12s), 0, "1", header_type="fixed", crc_bytes=4,
                                   payload_lengths=(32, 42))
            expected = t12s if capture["kind"] == "pairing_ack" else inverse
            self.assertEqual(packet.sync_word, expected)
            self.assertEqual(packet.crc_initial, 0)
            self.assertEqual(packet.payload.hex(), capture["payload_hex"])
            self.assertEqual(packet.sync_crc_candidates, ((expected, 0),))

    def test_format_limits_and_dynamic_window(self):
        for values in ({"header_type": "implicit"}, {"payload_lengths": (5,)}, {"payload_lengths": (128,)},
                       {"crc_bytes": 0}, {"crc_bytes": 1}, {"crc_bytes": 2, "crc_initial": 0x10000},
                       {"crc_initials": (-1,)}, {"crc_initials": (), "crc_initial": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DemodConfig(**values)
        for header, header_bits in (("fixed", 0), ("variable", 16)):
            config = DemodConfig(header_type=header, crc_bytes=4, coding_rate="1/2")
            maximum = coded_length(header_bits + 8 * (127 + 4) + tail_length("1/2"), "1/2")
            self.assertGreaterEqual(config.max_coded_bits, maximum)
            self.assertGreater(config.overlap_seconds, (85 + maximum) / config.bit_rate)
            with self.assertRaises(ValueError):
                replace(config, max_coded_bits=maximum - 1)

    def test_h12_template_and_cli_override_precedence(self):
        template = ROOT / "templates/physical/h12.toml"
        config = load_config(template, {})
        self.assertEqual(config.payload_lengths, (32, 42, 97, 127))
        self.assertEqual(config.packet_format.crc_seeds, (0xFFFFFFFF, 0))
        self.assertEqual(config.filter_cutoff, 750000)
        self.assertEqual(DemodConfig().crc_initial, 0xFFFFFF)
        self.assertEqual(DemodConfig(crc_bytes=2).crc_initial, 0xFFFF)
        args = make_argument_parser().parse_args([
            "--iq", "unused.iq", "--config", str(template), "--crc-initial", "0",
            "--payload-length", "42", "--filter-cutoff", "800000",
        ])
        config = load_config(args.config, demod_overrides(args))
        self.assertEqual(config.packet_format.crc_seeds, (0,))
        self.assertEqual(config.payload_lengths, (42,))
        self.assertEqual(config.filter_cutoff, 800000)
        self.assertEqual(load_config(template, {"crc_initials": [123, 456]}).packet_format.crc_seeds, (123, 456))

    def test_iq_roundtrip_crc32_fixed_with_edge_wrap_and_window_boundary(self):
        # 信号中心距 Nyquist 只有 380kHz，频谱跨过文件频带边缘。
        config = ModulationConfig(sample_rate=8e6, bit_rate=1.3e6, offset_hz=0, bt=0,
                                  coding_rate="1", guard_seconds=.0015, repeat=2)
        packet = encode_packet(bytes(range(127)), b"TEST", 0, "1", header_type="fixed", crc_bytes=4)
        def wrapped_signal():
            cursor = 0
            for chunk in iter_signal(packet.bits, config):
                indices = cursor + np.arange(len(chunk))
                yield chunk * np.exp(-2j * np.pi * 3.62e6 * indices / config.sample_rate)
                cursor += len(chunk)
        demod = DemodConfig(sample_rate=8e6, center_frequency=2403e6, frequencies=(2399.38e6,),
                            bit_rate=1.3e6, coding_rate="1", bt=0, filter_cutoff=750000,
                            header_type="fixed", crc_bytes=4, crc_initials=(0xFFFFFFFF, 0),
                            payload_lengths=(32, 42, 97, 127), chunk_seconds=.002, allow_frequency_wrap=True)
        with self.assertRaises(ValueError):
            replace(demod, allow_frequency_wrap=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrapped.iq"
            write_iq(path, wrapped_signal(), "cs8")
            frames = list(iter_frames(path, demod))
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(frame.packet.payload == packet.payload for frame in frames))


@unittest.skipUnless(os.environ.get("FLRC_TEST_H12_IQ"), "未设置外部 IQ 路径 FLRC_TEST_H12_IQ")
class H12CapturedIQTests(unittest.TestCase):
    def test_capture_contains_all_four_golden_packets(self):
        config = load_config(ROOT / "templates/physical/h12.toml", {})
        frames = list(iter_frames(Path(os.environ["FLRC_TEST_H12_IQ"]), config))
        for capture in CAPTURES:
            with self.subTest(kind=capture["kind"]):
                self.assertTrue(any(abs(frame.time_s - capture["source_time_s"]) < .0001
                                    and frame.packet.payload.hex() == capture["payload_hex"]
                                    and frame.packet.crc.hex() == capture["crc_hex"] for frame in frames))
