"""协议字段回归：真实捕获、双向差异、模式选择和失败隔离。"""
import json
from pathlib import Path
import struct
import unittest

from flrc_demod.io.output import build_record
from flrc_demod.models import DecodedFrame, PhysicalPacket
from flrc_demod.parsing.api import load_parser
from flrc_demod.phy.crc import crc24

FIXTURES = Path(__file__).parent / "fixtures"


def frame_from_payload(payload: bytes) -> DecodedFrame:
    return DecodedFrame(0.01, 2480060000, 0, -10, 1, 0,
                        PhysicalPacket(b"1234", bytes((0, len(payload))), payload, b"123", 0))


def sample(command, data=b"", *, timestamp=None, need_ack=False, is_ack=False):
    """构造应用层报文；这里不伪装成真实 IQ/硬件 CRC 捕获。"""
    seq_flags = (17 << 1) | is_ack
    small = bytes((len(data) | (0x80 if need_ack else 0),)) + seq_flags.to_bytes(2, "little")
    small += bytes((command,)) + data
    small += bytes((sum(small) & 255,))
    body = (timestamp.to_bytes(4, "little") if timestamp is not None else b"") + small
    return frame_from_payload(bytes((len(body),)) + body)


def packed_channels(values):
    return sum(value << (11 * i) for i, value in enumerate(values)).to_bytes(22, "little")


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.dk32s = load_parser("dk32s")
        self.datalink = load_parser("datalink")

    def test_dk32s_recorded_pairing_offer(self):
        fixture = json.loads((FIXTURES / "dk32s_packet.json").read_text())
        result = self.dk32s(frame_from_payload(bytes.fromhex(fixture["payload_hex"])))
        self.assertEqual(result["protocol"], "dk32s")
        self.assertEqual(result["direction"], "ground_to_air")
        self.assertEqual(result["timestamp_ticks"], 291370)
        self.assertEqual(result["sequence"], 888)
        self.assertEqual(result["fields"]["filter_address"], "0x3F2B0AE4")
        self.assertEqual(result["fields"]["channel_indices"],
                         [194, 191, 141, 33, 45, 79, 156, 129, 2, 225, 87, 175, 102, 206, 60, 52, 18, 110, 144, 232])

    def test_datalink_recorded_bidirectional_data(self):
        for fixture in json.loads((FIXTURES / "datalink_packets.json").read_text()):
            with self.subTest(direction=fixture["direction"]):
                crc_input = bytes.fromhex(fixture["sync_hex"] + fixture["header_hex"] + fixture["payload_hex"])
                self.assertEqual(crc24(crc_input), int(fixture["crc_hex"], 16))
                result = self.datalink(frame_from_payload(bytes.fromhex(fixture["payload_hex"])))
                self.assertEqual(result["direction"], fixture["direction"])
                self.assertEqual(result["sequence"], fixture["sequence"])
                self.assertEqual(result["fields"]["data_batch_id"], fixture["batch_id"])
                self.assertEqual(result["fields"]["stream_data_hex"], "")

    def test_pairing_offer_directions_differ(self):
        data = bytes.fromhex("e40a2b3f") + bytes(range(20))
        dk = self.dk32s(sample(0x13, data, timestamp=0, need_ack=True))
        dl = self.datalink(sample(0x13, data, need_ack=True))
        self.assertEqual(dk["direction"], "ground_to_air")
        self.assertEqual(dk["timestamp_ticks"], 0)
        self.assertEqual(dl["direction"], "air_to_ground")

    def test_pairing_ack_directions_differ(self):
        frame = sample(0x13, bytes.fromhex("e40a2b3f"), is_ack=True)
        self.assertEqual(self.dk32s(frame)["direction"], "air_to_ground")
        self.assertEqual(self.datalink(frame)["direction"], "ground_to_air")

    def test_time_fields_have_protocol_specific_types(self):
        dk = self.dk32s(sample(0x17, (-53).to_bytes(4, "little", signed=True), timestamp=1, need_ack=True))
        dl = self.datalink(sample(0x17, struct.pack("<f", 0.0056), need_ack=True))
        self.assertEqual(dk["fields"]["correction_ticks"], -53)
        self.assertAlmostEqual(dl["fields"]["transfer_delay_sec"], 0.0056, places=8)

    def test_normal_rc_is_not_guessed_as_multi(self):
        values = [0, 1, 2, 100, 2047] + [1024] * 11
        frame = sample(0x11, packed_channels(values) + b"\xaa\xbb", timestamp=10)
        fields = self.dk32s(frame)["fields"]
        self.assertEqual(fields["channels_raw"], values)
        self.assertEqual(fields["channels_output"], [value + 500 for value in values])
        self.assertEqual(fields["data_batch_id"], 0xAA)
        self.assertEqual(fields["stream_data_hex"], "bb")
        self.assertNotIn("device_number", fields)

    def test_multi_rc_explicit_prefix(self):
        values = list(range(16))
        frame = sample(0x16, b"\x05\x02" + packed_channels(values) + b"\x07hello", timestamp=100)
        fields = load_parser("dk32s-multi")(frame)["fields"]
        self.assertEqual(fields["target_device_mask"], 5)
        self.assertEqual(fields["device_number"], 2)
        self.assertEqual(fields["channels_raw"], values)
        self.assertEqual(fields["stream_data_hex"], b"hello".hex())

    def test_dk32s_telemetry_with_voltage(self):
        fields = self.dk32s(sample(0x46, b"\x07ABC\x34\x12", is_ack=True))["fields"]
        self.assertEqual(fields["data_batch_id"], 7)
        self.assertEqual(fields["stream_data_hex"], "414243")
        self.assertEqual(fields["voltage_raw"], 0x1234)

    def test_config_commands_and_empty_ack(self):
        dk = self.dk32s(sample(0x40, b"\x05", timestamp=10, need_ack=True))
        dl = self.datalink(sample(0xF7, b"\x05", is_ack=True))
        self.assertEqual(dk["fields"]["baud_rate"], 115200)
        self.assertEqual(dl["fields"]["baud_rate"], 115200)
        self.assertEqual(dl["direction"], "ground_to_air")
        self.assertEqual(self.dk32s(sample(0x40, is_ack=True))["fields"]["kind"], "ack")

    def test_failsafe_bitfields(self):
        values = [1000] * 16
        data = b"\x02\x05\x00" + packed_channels(values)
        fields = self.dk32s(sample(0x12, data, timestamp=10, need_ack=True))["fields"]
        self.assertEqual(fields["channel_mask"], 5)
        self.assertEqual(fields["channel_enabled"][:4], [True, False, True, False])
        self.assertEqual(fields["channels_output"], [1500] * 16)

    def test_pwm_map_and_opaque_video_values(self):
        packed = sum(value << (5 * i) for i, value in enumerate(range(1, 10)))
        mapping = self.dk32s(sample(0x21, packed.to_bytes(6, "little") + b"\xaa" * 6, is_ack=True))
        self.assertEqual(mapping["fields"]["channel_mapping"], list(range(1, 10)))
        data = b"".join(value.to_bytes(4, "little") for value in (1, 256, 65536))
        video = self.dk32s(sample(0x62, data, timestamp=10, need_ack=True))
        self.assertEqual(video["fields"]["parameters_u32"], [1, 256, 65536])

    def test_multi_pairing_and_searchlight(self):
        data = b"\x01\x03" + bytes.fromhex("1122334455667788aabbccdd") + bytes(range(20))
        fields = self.dk32s(sample(0x60, data, timestamp=1, need_ack=True))["fields"]
        self.assertEqual(fields["device_number"], 3)
        self.assertEqual(fields["filter_address"], "0xDDCCBBAA")
        light = self.dk32s(sample(0x64, b"\x01\x06\x32", is_ack=True))["fields"]
        self.assertEqual(light["brightness_level"], 50)

    def test_unknown_command_keeps_raw_body(self):
        result = self.dk32s(sample(0xA9, b"\x12\x34", timestamp=10))
        self.assertEqual(result["fields"], {"kind": "unknown", "raw_hex": "1234"})
        self.assertEqual(result["sequence"], 17)

    def test_checksum_length_and_business_errors_preserve_physical_frame(self):
        valid = sample(0x13, b"short", timestamp=10, need_ack=True)
        original = bytearray(valid.packet.payload)
        bad_sum, bad_length = original.copy(), original.copy()
        bad_sum[-1] ^= 1
        bad_length[0] -= 1
        for frame in (valid, frame_from_payload(bad_sum), frame_from_payload(bad_length)):
            with self.subTest(payload=frame.packet.payload):
                record = build_record(frame, self.dk32s)
                self.assertIn("application_error", record)
                self.assertTrue(record["crc_ok"])
                self.assertEqual(record["payload_hex"], frame.packet.payload.hex())

    def test_datalink_wrong_layout_is_rejected(self):
        fixture = json.loads((FIXTURES / "dk32s_packet.json").read_text())
        record = build_record(frame_from_payload(bytes.fromhex(fixture["payload_hex"])), self.datalink)
        self.assertIn("application_error", record)
        self.assertNotIn("application", record)

    def test_nonfinite_datalink_time_is_not_serialized(self):
        frame = sample(0xF2, struct.pack("<f", float("nan")) + b"\x01", need_ack=True)
        self.assertIn("application_error", build_record(frame, self.datalink))

    def test_builtin_names(self):
        from flrc_demod.parsing.protocols import BUILTIN_PARSERS
        self.assertEqual(set(BUILTIN_PARSERS), {"dk32s", "dk32s-multi", "datalink"})
