import io
import json
import unittest

from flrc_modem.io.output import build_record, write_jsonl
from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.parsing.api import load_parser


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.frame = DecodedFrame(0.01, 2480060000, 7000, -10, 1.1, 0,
                                  PhysicalPacket(b"1234", b"\0\6", b"ABCDEF", b"xyz", 1))

    def test_default_output_has_no_business_fields(self):
        record = build_record(self.frame)
        self.assertNotIn("application", record)
        self.assertEqual(record["payload_hex"], "414243444546")
        stream = io.StringIO()
        write_jsonl(stream, record)
        self.assertEqual(json.loads(stream.getvalue()), record)

    def test_optional_plugin_is_separate(self):
        parser = load_parser("flrc_modem.parsing.example:describe_payload")
        record = build_record(self.frame, parser)
        self.assertEqual(record["application"]["byte_count"], 6)
        self.assertEqual(record["payload_hex"], "414243444546")

    def test_broken_plugin_keeps_raw_packet(self):
        def broken(frame):
            raise RuntimeError("示例解析失败")
        for parser in (broken, lambda frame: {"bad": float("nan")}, lambda frame: [1]):
            with self.subTest(parser=parser):
                record = build_record(self.frame, parser)
                self.assertIn("application_error", record)
                self.assertTrue(record["crc_ok"])
                self.assertEqual(record["payload_hex"], "414243444546")
