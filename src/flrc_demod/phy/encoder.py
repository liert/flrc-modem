"""原始载荷封装成 FLRC 判决域比特，不生成厂商业务字段。"""
from dataclasses import dataclass
import numpy as np

from .crc import crc24
from .fec import convolutional_encode
from .sync import TIMING_PREAMBLE
from .profile import tail_length


@dataclass(frozen=True)
class EncodedPacket:
    sync_word: bytes
    header: bytes
    payload: bytes
    crc: bytes
    bits: np.ndarray


def _differential_section(bits: np.ndarray) -> np.ndarray:
    # 从同步字到编码数据是同一条连续差分链，初始相位对应 0x99。
    # 接收机分别尝试 0x66/0x99 会掩盖首位错误，不能直接反用解码候选构造空口序列。
    mask = np.unpackbits(np.array([0x99], dtype=np.uint8))
    masked = bits ^ np.resize(mask, len(bits))
    raw = masked.copy()
    raw[1:] ^= masked[:-1]
    return raw


def encode_packet(payload: bytes, sync_word: bytes, crc_initial: int = 0xFFFFFF,
                  coding_rate: str = "3/4") -> EncodedPacket:
    """生成 32bit AGC + 21bit 定时前导 + 32bit 同步区 + 编码包。"""
    payload, sync_word = bytes(payload), bytes(sync_word)
    if not 6 <= len(payload) <= 127:
        raise ValueError("当前变长物理载荷必须为 6..127B")
    if len(sync_word) != 4:
        raise ValueError("同步字必须为 4B")
    if not isinstance(crc_initial, int) or not 0 <= crc_initial <= 0xFFFFFF:
        raise ValueError("CRC24 初值必须为 0..0xFFFFFF")
    header = bytes((0, len(payload)))
    checksum = crc24(sync_word + header + payload, crc_initial).to_bytes(3, "big")
    data_bits = np.unpackbits(np.frombuffer(header + payload + checksum, dtype=np.uint8))
    # 有 FEC 时追加六个零回到零末态；CR=1 无 FEC，也没有尾比特。
    coded = convolutional_encode(np.concatenate((data_bits, np.zeros(tail_length(coding_rate), dtype=np.uint8))),
                                 coding_rate)
    bits = np.concatenate((
        np.tile(np.array([0, 1], dtype=np.uint8), 16), TIMING_PREAMBLE,
        _differential_section(np.concatenate((np.unpackbits(np.frombuffer(sync_word, dtype=np.uint8)), coded))),
    ))
    return EncodedPacket(sync_word, header, payload, checksum, bits)
