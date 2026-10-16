"""只恢复物理包。载荷首字节是不是长度、是否有时间戳均不在此处理。"""
import numpy as np

from ..models import PhysicalPacket
from .crc import crc24
from .fec import coded_length, convolutional_decode
from .sync import recover_coded_bits
from .profile import tail_length


def decode_packet(raw_bits, sync_candidates, crc_initial: int = 0xFFFFFF,
                  coding_rate: str = "3/4") -> PhysicalPacket | None:
    tail_bits = tail_length(coding_rate)
    for mask in (0x66, 0x99):
        coded = recover_coded_bits(raw_bits, mask)
        if len(coded) < coded_length(16, coding_rate):
            continue
        prefix, _ = convolutional_decode(coded[:min(len(coded), 160)], coding_rate=coding_rate)
        header = np.packbits(prefix[:16]).tobytes()
        size = header[1] & 0x7F
        if header[0] or header[1] & 0x80 or not 6 <= size <= 127:
            continue
        steps = 16 + 8 * size + 24 + tail_bits
        required = coded_length(steps, coding_rate)
        if len(coded) < required:
            continue
        decoded, metric = convolutional_decode(coded[:required], steps, terminated=True, coding_rate=coding_rate)
        data = np.packbits(decoded[:16 + 8 * size + 24]).tobytes()
        expected_crc = int.from_bytes(data[-3:], "big")
        for sync in dict.fromkeys(sync_candidates):
            if len(sync) != 4:
                raise ValueError("同步字必须为 4B")
            if crc24(sync + data[:-3], crc_initial) == expected_crc:
                return PhysicalPacket(sync, data[:2], data[2:2 + size], data[-3:], metric)
    return None
