"""业务编码的抓包基准、规则变化、完整 IQ 回读与发送生命周期。"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from flrc_modem.encoding import EncodeContext, MessageTemplate, load_encoder
from flrc_modem.encoding.dk32s import encode
from flrc_modem.encoding.input import iter_message_records
from flrc_modem.modulation import modulate_bits
from flrc_modem.parsing.api import load_parser
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.sdr.hackrf import HackRFDevice
from flrc_modem.transmission import LiveTransmitter, PacketSource
from flrc_modem.transmit_cli import main
from hackrf_fake import FakeAPI
from test_protocols import frame_from_payload
from test_transmission import MODULATION, HARDWARE, collect, decode

CONTEXT = EncodeContext(0, 0, 2_000_000)
RC = {"direction": "ground_to_air", "command": "0x11",
      "fields": {"channels_raw": [1024] * 16}}


def parse(payload, multi=False):
    return load_parser("dk32s-multi" if multi else "dk32s")(frame_from_payload(payload))


class EncoderTests(unittest.TestCase):
    def test_recorded_pairing_offer_and_physical_crc_match_exactly(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/dk32s_packet.json").read_text())
        message = {"direction": "ground_to_air", "command": "0x13", "sequence": 888,
                   "timestamp_ticks": 291370, "need_ack": True,
                   "fields": {"filter_address": "0x3F2B0AE4", "channel_indices":
                       [194, 191, 141, 33, 45, 79, 156, 129, 2, 225, 87, 175, 102, 206, 60, 52, 18, 110, 144, 232]}}
        payload = load_encoder("dk32s")(message, CONTEXT)
        self.assertEqual(payload.hex(), fixture["payload_hex"])
        packet = encode_packet(payload, bytes.fromhex(fixture["sync_hex"]))
        self.assertEqual(packet.header.hex(), fixture["header_hex"])
        self.assertEqual(packet.crc.hex(), fixture["crc_hex"])

    def test_channels_bit_order_boundaries_and_multi_prefix(self):
        values = [0, 1, 2047, 512, 1023, 1024, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21]
        for multi in (False, True):
            message = deepcopy(RC)
            message["fields"]["channels_raw"] = values
            message["fields"].update(data_batch_id=255, stream_data_hex="01abcd")
            if multi:
                message["rc_mode"] = "multi"
                message["fields"].update(target_device_mask=3, device_number=1)
            fields = parse(encode(message, CONTEXT), multi)["fields"]
            self.assertEqual(fields["channels_raw"], values)
            self.assertEqual(fields["stream_data_hex"], "01abcd")
            if multi:
                self.assertEqual(fields["target_device_mask"], 3)
                self.assertEqual(fields["device_number"], 1)

    def test_other_typed_fields_both_directions(self):
        cases = [
            ("air_to_ground", 0x13, {"filter_address": "0x11223344"}),
            ("air_to_ground", 0x16, {"data_batch_id": 5, "stream_data_hex": "01abcd"}),
            ("air_to_ground", 0x46, {"data_batch_id": 7, "voltage_raw": 65535}),
            ("ground_to_air", 0x17, {"correction_ticks": -500}),
            ("ground_to_air", 0x18, {"mode": 1, "sync_count": 3}),
            ("ground_to_air", 0x40, {"baud_index": 5}),
            ("air_to_ground", 0x40, {}),
            ("air_to_ground", 0xF0, {"version_hex": "0x00010005"}),
        ]
        for direction, command, fields in cases:
            message = {"direction": direction, "command": command, "fields": fields,
                       "sequence": 12, "need_ack": direction == "ground_to_air", "is_ack": direction == "air_to_ground"}
            with self.subTest(command=command, direction=direction):
                result = parse(encode(message, CONTEXT))
                self.assertEqual(result["sequence"], 12)
                self.assertEqual(result["direction"], direction)
                for key, value in fields.items():
                    self.assertEqual(str(result["fields"][key]).lower(), str(value).lower())

    def test_vice_pairing_and_rc_relay_commands(self):
        # 0x54: 开启/关闭遥控接力 (支持 rc_relay / rc_relay_switch / vice_match_mode / vice_pairing 别名)
        for key in ("rc_relay", "rc_relay_switch", "vice_match_mode", "vice_pairing"):
            msg = {"direction": "ground_to_air", "command": 0x54, "fields": {key: 1}, "need_ack": True}
            result = parse(encode(msg, CONTEXT))
            self.assertEqual(result["fields"]["kind"], "config_set")
            self.assertEqual(result["fields"]["vice_pairing"], 1)

        ack_54 = {"direction": "air_to_ground", "command": 0x54, "is_ack": True, "fields": {}}
        self.assertEqual(parse(encode(ack_54, CONTEXT))["fields"]["kind"], "ack")

        # 0x55: 切换主副 (支持 switch_master_slave/switch_vice/rc_relay/vice_pairing/vice_match_mode/release_rc_relay 以及空 fields)
        for key in ("switch_master_slave", "switch_vice", "rc_relay", "vice_pairing", "vice_match_mode", "release_rc_relay"):
            msg = {"direction": "ground_to_air", "command": 0x55, "fields": {key: 1}, "need_ack": True}
            result = parse(encode(msg, CONTEXT))
            self.assertEqual(result["fields"]["kind"], "switch_master_slave")
            self.assertEqual(result["fields"]["vice_pairing"], 1)

        empty_55 = {"direction": "ground_to_air", "command": 0x55, "fields": {}, "need_ack": True}
        result = parse(encode(empty_55, CONTEXT))
        self.assertEqual(result["fields"]["kind"], "switch_master_slave")
        self.assertNotIn("vice_pairing", result["fields"])

        ack_55 = {"direction": "air_to_ground", "command": 0x55, "is_ack": True, "fields": {}}
        self.assertEqual(parse(encode(ack_55, CONTEXT))["fields"]["kind"], "ack")

    def test_auto_sequence_timestamp_wrap_and_explicit_fixed_values(self):
        context = EncodeContext(32768, (2**32 + 10000) * 200, 2_000_000)
        result = parse(encode(RC, context))
        self.assertEqual(result["sequence"], 0)
        self.assertEqual(result["timestamp_ticks"], 10000)
        message = {**RC, "sequence": 31, "timestamp_ticks": 42}
        result = parse(encode(message, context))
        self.assertEqual((result["sequence"], result["timestamp_ticks"]), (31, 42))

    def test_counter_cycle_clock_rules_and_template_immutability(self):
        message = deepcopy(RC)
        message.update(sequence=0, timestamp_ticks=0, updates={
            "sequence": {"type": "counter", "start": 32767, "modulo": 32768},
            "timestamp_ticks": {"type": "clock", "start": 100, "rate": 10000, "modulo": 2**32},
            "fields.channels_raw.0": {"type": "cycle", "values": [0, 1024, 2047]},
            "fields.data_batch_id": {"type": "counter", "start": 255, "modulo": 256}})
        message["fields"]["data_batch_id"] = 0
        original = deepcopy(message)
        template = MessageTemplate(encode, message)
        results = [parse(template.encode(EncodeContext(i, 2_000_000 + i * 200, 2_000_000))) for i in range(3)]
        self.assertEqual([r["sequence"] for r in results], [32767, 0, 1])
        self.assertEqual([r["timestamp_ticks"] for r in results], [10100, 10101, 10102])
        self.assertEqual([r["fields"]["channels_raw"][0] for r in results], [0, 1024, 2047])
        self.assertEqual([r["fields"]["data_batch_id"] for r in results], [255, 0, 1])
        self.assertEqual(message, original)

    def test_bad_fields_and_unused_typos_rejected(self):
        invalid = [
            {**RC, "sequence": True}, {**RC, "need_ack": 1}, {**RC, "direction": "ground"},
            {**RC, "sequnce": 1}, {**RC, "fields": {"channels_raw": [0] * 15}},
            {**RC, "fields": {"channels_raw": [2048] * 16}},
            {**RC, "fields": {"channels_raw": [0] * 16, "stream_data_hex": "01"}},
            {**RC, "fields": {"data_hex": "00", "channels_raw": [0] * 16}},
            {"direction": "air_to_ground", "command": 0x55, "timestamp_ticks": 0, "fields": {"data_hex": ""}},
        ]
        for message in invalid:
            with self.subTest(message=message), self.assertRaises(ValueError):
                MessageTemplate(encode, message).encode(CONTEXT)

    def test_raw_business_escape_and_firmware_length_limits(self):
        for direction, maximum in (("ground_to_air", 105), ("air_to_ground", 109)):
            message = {"direction": direction, "command": 0xEE, "fields": {"data_hex": "ab" * maximum}}
            result = parse(encode(message, CONTEXT))
            self.assertEqual(result["payload_hex"], "ab" * maximum)
            message["fields"]["data_hex"] += "ab"
            with self.assertRaises(ValueError):
                encode(message, CONTEXT)

    def test_invalid_rules_and_missing_paths(self):
        for path, rule in (("fields.typo", {"type": "counter"}),
                           ("sequence", {"type": "counter", "modulo": 0}),
                           ("sequence", {"type": "counter", "step": True}),
                           ("sequence", {"type": "cycle", "values": []}),
                           ("sequence", {"type": "clock", "rate": float("nan")}),
                           ("sequence", {"type": "counter", "stpe": 2}),
                           ("sequence", {"type": "eval", "code": "1"})):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                MessageTemplate(encode, {**RC, "sequence": 0, "updates": {path: rule}}).encode(CONTEXT)

    def test_external_plugin_loading_and_contract(self):
        self.assertIs(load_encoder("flrc_modem.encoding.dk32s:encode"), encode)
        for spec in ("datalink", "no.such.module:encode", "flrc_modem.encoding.dk32s:missing",
                     "flrc_modem.encoding.dk32s:SETTINGS"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                load_encoder(spec)
        with self.assertRaises(ValueError):
            MessageTemplate(lambda m, c: "not bytes", {}).encode(CONTEXT)


class MessageTransmissionTests(unittest.TestCase):
    def test_repeat_reencodes_at_sample_clock_and_across_templates(self):
        source = PacketSource(MODULATION, b"TEST")
        message = deepcopy(RC)
        message["fields"]["data_batch_id"] = 0
        message["updates"] = {"fields.data_batch_id": {"type": "counter", "modulo": 256}}
        source.submit_message(message, encode, repeat=2)
        source.submit_message(message, encode, repeat=1)
        source.finish()
        frames = decode(collect(source, size=997))
        results = [parse(frame.packet.payload) for frame in frames]
        self.assertEqual([row["sequence"] for row in results], [0, 1, 2])
        self.assertEqual([row["fields"]["data_batch_id"] for row in results], [0, 1, 2])
        # 精确按调制波形长度、间隔及前导位置计算时间，不依赖 CPU/USB 墙钟。
        burst_size = len(modulate_bits(encode_packet(frames[0].packet.payload, b"TEST").bits, MODULATION))
        start = round(MODULATION.guard_seconds * MODULATION.sample_rate) + MODULATION.ramp_samples
        stride = burst_size + round(MODULATION.gap_seconds * MODULATION.sample_rate)
        self.assertEqual([row["timestamp_ticks"] for row in results],
                         [int((start + i * stride) / MODULATION.sample_rate * 10000) for i in range(3)])

    def test_idle_and_raw_packets_are_part_of_clock_and_global_index(self):
        source = PacketSource(MODULATION, b"TEST")
        idle = bytearray(40000)
        source.readinto(memoryview(idle))
        source.submit(b"123456")
        source.submit_message(RC, encode)
        source.finish()
        frames = decode(bytes(idle) + collect(source))
        result = parse(frames[-1].packet.payload)
        self.assertEqual(result["sequence"], 1)
        self.assertGreaterEqual(result["timestamp_ticks"], 100)

    def test_runtime_plugin_failure_stops_and_releases_device(self):
        api = FakeAPI(buffer_size=4096)
        def fail_later(message, context):
            if context.frame_index == 1:
                raise RuntimeError("测试动态字段错误")
            return encode(message, context)
        sender = LiveTransmitter(MODULATION, HARDWARE, b"TEST", device_factory=lambda: HackRFDevice(api))
        sender.submit_message(RC, fail_later, repeat=2)
        sender.finish()
        with self.assertRaisesRegex(Exception, "动态字段错误"), sender:
            sender.wait()
        self.assertEqual(sender.stats.status, "failed")
        self.assertEqual(api.calls[-1], "release")

    def run_cli(self, args, text=""):
        api, stdout, stderr = FakeAPI(buffer_size=4096, tx_interval=0.001), io.StringIO(), io.StringIO()
        with patch("flrc_modem.transmission.session.HackRFLibrary", return_value=api), \
                patch("sys.stdin", io.StringIO(text)), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--frequency", "2480000000", "--sync", "54455354", *args])
        return code, api, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def test_protocol_jsonl_repeats_and_per_row_sync(self):
        text = "\n".join(json.dumps(row) for row in (
            {"message": RC, "repeat": 2}, {"message": RC, "sync": "4e455854"}))
        code, api, stats, error = self.run_cli(["--protocol", "dk32s", "--stdin-jsonl"], text)
        self.assertEqual(code, 0, error)
        frames = decode(api.sent)
        self.assertEqual([parse(frame.packet.payload)["sequence"] for frame in frames], [0, 1, 2])
        self.assertEqual(frames[-1].packet.sync_word, b"NEXT")
        self.assertEqual(stats["status"], "completed")
        self.assertIn("发送插件=dk32s", error)

    def test_template_file_finite_and_continuous_modes(self):
        fixture = Path(__file__).resolve().parents[1] / "templates/messages/dk32s_tx_channels.json"
        for options in (["--repeat", "2"], ["--repeat", "0", "--duration", "0.05"]):
            code, api, stats, error = self.run_cli(["--protocol", "dk32s", "--message-file", str(fixture), *options])
            with self.subTest(options=options):
                self.assertEqual(code, 0, error)
                if options[1] == "2":
                    self.assertEqual([parse(frame.packet.payload)["sequence"] for frame in decode(api.sent)], [0, 1])
                else:
                    self.assertEqual(stats["status"], "duration_reached")

    def test_invalid_first_message_never_opens_device(self):
        for args, text in ((["--protocol", "dk32s", "--stdin-jsonl"], '{"message":{}}'),
                           (["--protocol", "dk32s", "--payload-hex", "000102030405"], ""),
                           (["--protocol", "datalink", "--stdin-jsonl"], "")):
            code, api, _, error = self.run_cli(args, text)
            with self.subTest(args=args):
                self.assertEqual(code, 2)
                self.assertNotIn("open", api.calls)
                self.assertTrue(error)

    def test_bad_later_jsonl_and_bad_envelopes(self):
        text = json.dumps({"message": RC}) + '\n{"message":{},"repeta":1}'
        code, api, stats, error = self.run_cli(["--protocol", "dk32s", "--stdin-jsonl"], text)
        self.assertEqual(code, 2)
        self.assertIn("第 2 行", error)
        self.assertEqual(stats["status"], "failed")
        for text in ('[]', '{"message":{},"repeat":true}', '{"message":{},"sync":null}', "x" * 65537):
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                list(iter_message_records(io.StringIO(text), sync_word=b"TEST"))
