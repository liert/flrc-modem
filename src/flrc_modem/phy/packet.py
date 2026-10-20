"""只恢复物理包。载荷首字节是不是长度、是否有时间戳均不在此处理。"""
import numpy as np

from ..models import PhysicalPacket
from .crc import flrc_crc
from .fec import coded_length, convolutional_decode
from .sync import recover_coded_bits
from .profile import tail_length
from .packet_format import PacketFormat


def decode_packet(raw_bits, sync_candidates, crc_initial: int | None = None,
                  coding_rate: str = "3/4", *, header_type: str = "variable",
                  payload_lengths=(), crc_bytes: int = 3, crc_initials=()) -> PhysicalPacket | None:
    packet_format = PacketFormat(header_type, payload_lengths, crc_bytes, crc_initial, crc_initials)
    syncs = tuple(dict.fromkeys(sync_candidates))
    if any(len(sync) != 4 for sync in syncs):
        raise ValueError("同步字必须为 4B")
    tail_bits = tail_length(coding_rate)
    for mask in (0x66, 0x99):
        coded = recover_coded_bits(raw_bits, mask)
        layouts = []
        if header_type in ("variable", "auto") and len(coded) >= coded_length(16, coding_rate):
            prefix, _ = convolutional_decode(coded[:min(len(coded), 160)], coding_rate=coding_rate)
            header = np.packbits(prefix[:16]).tobytes()
            size = header[1] & 0x7F
            if not header[0] and not header[1] & 0x80 and 6 <= size <= 127:
                layouts.append((2, size))
        if header_type in ("fixed", "auto"):
            layouts.extend((0, size) for size in packet_format.fixed_lengths)
        for header_bytes, size in layouts:
            data_bits = 8 * (header_bytes + size + crc_bytes)
            steps = data_bits + tail_bits
            required = coded_length(steps, coding_rate)
            if len(coded) < required:
                continue
            decoded, metric = convolutional_decode(coded[:required], steps, terminated=True, coding_rate=coding_rate)
            data = np.packbits(decoded[:data_bits]).tobytes()
            # 完整 Viterbi 结果也必须保留合法的变长头。
            if header_bytes and data[:2] != bytes((0, size)):
                continue
            expected_crc = int.from_bytes(data[-crc_bytes:], "big")
            matches = tuple((sync, seed) for sync in syncs for seed in packet_format.crc_seeds
                            if flrc_crc(sync + data[:-crc_bytes], crc_bytes, seed) == expected_crc)
            if matches:
                sync, seed = matches[0]
                return PhysicalPacket(sync, data[:header_bytes], data[header_bytes:header_bytes + size],
                                      data[-crc_bytes:], metric, seed, matches)
    return None
