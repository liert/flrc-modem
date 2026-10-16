"""真实捕获为金标准，另用任意业务字节验证物理层边界。"""
import json
from pathlib import Path
import unittest
import numpy as np

from flrc_demod.phy.crc import crc24
from flrc_demod.phy.fec import convolutional_decode
from flrc_demod.phy.packet import decode_packet

FIXTURE = json.loads((Path(__file__).parent / "fixtures/dk32s_packet.json").read_text())


def encode_for_test(data: bytes):
    """独立的测试参考编码器，用于构造合法包和损坏 CRC 的负样本。"""
    input_bits = list(np.unpackbits(np.frombuffer(data, dtype=np.uint8))) + [0] * 6
    output, state = [], 0
    selection = ((0, 1), (0,), (1,))
    for i, bit in enumerate(input_bits):
        state = ((state << 1) | int(bit)) & 127
        pair = ((state & 0x6D).bit_count() & 1, (state & 0x4F).bit_count() & 1)
        output.extend(pair[j] for j in selection[i % 3])
    coded = np.array(output, dtype=np.uint8)
    masked = coded ^ np.resize(np.unpackbits(np.array([0x66], dtype=np.uint8)), len(coded))
    # 累积异或的逆运算，生成接收判决域输入。
    raw = masked.copy()
    raw[1:] ^= masked[:-1]
    return raw


class PhysicalLayerTests(unittest.TestCase):
    def test_recorded_packet(self):
        raw = np.unpackbits(np.frombuffer(bytes.fromhex(FIXTURE["coded_hex"]), dtype=np.uint8))
        packet = decode_packet(raw, (bytes.fromhex(FIXTURE["sync_hex"]),))
        self.assertIsNotNone(packet)
        self.assertEqual(packet.header.hex(), FIXTURE["header_hex"])
        self.assertEqual(packet.payload.hex(), FIXTURE["payload_hex"])
        self.assertEqual(packet.crc.hex(), FIXTURE["crc_hex"])

    def test_recorded_crc_golden_value(self):
        data = bytes.fromhex(FIXTURE["sync_hex"] + FIXTURE["header_hex"] + FIXTURE["payload_hex"])
        self.assertEqual(crc24(data), 0xCE8519)

    def test_wrong_sync_or_crc_seed_is_rejected(self):
        raw = np.unpackbits(np.frombuffer(bytes.fromhex(FIXTURE["coded_hex"]), dtype=np.uint8))
        self.assertIsNone(decode_packet(raw, (b"\x00" * 4,)))
        self.assertIsNone(decode_packet(raw, (bytes.fromhex(FIXTURE["sync_hex"]),), 0))

    def test_truncated_frame_is_rejected(self):
        raw = np.unpackbits(np.frombuffer(bytes.fromhex(FIXTURE["coded_hex"]), dtype=np.uint8))
        self.assertIsNone(decode_packet(raw[:200], (bytes.fromhex(FIXTURE["sync_hex"]),)))

    def test_arbitrary_application_payload_is_preserved(self):
        # 首字节 FE 不是任何有效软件长度，也没有应用 sum8，仍应通过物理 CRC。
        sync = bytes.fromhex("12345678")
        payload = bytes.fromhex("fe102030405060")
        body = bytes((0, len(payload))) + payload
        frame = body + crc24(sync + body).to_bytes(3, "big")
        packet = decode_packet(encode_for_test(frame), (sync,))
        self.assertIsNotNone(packet)
        self.assertEqual(packet.payload, payload)

    def test_bad_crc_is_not_output(self):
        sync = bytes.fromhex("12345678")
        body = bytes.fromhex("0006ff1020304050")
        wrong_crc = (crc24(sync + body) ^ 1).to_bytes(3, "big")
        self.assertIsNone(decode_packet(encode_for_test(body + wrong_crc), (sync,)))

    def test_viterbi_rejects_insufficient_bits(self):
        with self.assertRaises(ValueError):
            convolutional_decode([0, 1], steps=100)
