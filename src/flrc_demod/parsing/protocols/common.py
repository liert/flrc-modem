"""SIYI 紧凑帧的公共结构；业务方向与字段解释留给各设备模块。"""
from dataclasses import dataclass
import math
import struct

BAUD_RATES = (4800, 9600, 38400, 57600, 76800, 115200, 230400)


@dataclass(frozen=True)
class CompactFrame:
    command: int
    sequence: int
    need_ack: bool
    is_ack: bool
    data: bytes
    timestamp_ticks: int | None = None


def unpack_radio(payload: bytes, offsets: tuple[int, ...]) -> CompactFrame:
    if not payload or payload[0] != len(payload) - 1:
        raise ValueError("软件帧长与物理载荷长度不一致")
    matches = []
    checksum_failed = False
    for offset in offsets:
        small = payload[offset:]
        if len(small) < 5 or len(small) != (small[0] & 0x7F) + 5:
            continue
        if sum(small[:-1]) & 0xFF != small[-1]:
            checksum_failed = True
            continue
        seq_flags = int.from_bytes(small[1:3], "little")
        matches.append(CompactFrame(
            small[3], seq_flags >> 1, bool(small[0] & 0x80), bool(seq_flags & 1), small[4:-1],
            int.from_bytes(payload[1:5], "little") if offset == 5 else None,
        ))
    if len(matches) > 1:
        raise ValueError("载荷同时符合多个封装，拒绝猜测时间戳位置")
    if not matches:
        raise ValueError("sum8 校验失败" if checksum_failed else "紧凑帧声明长度与实际长度不一致")
    return matches[0]


def require_size(data: bytes, size: int, field: str):
    if len(data) != size:
        raise ValueError(f"{field} 应为 {size}B，实际为 {len(data)}B")


def unpack_bits_le(data: bytes, count: int, width: int) -> list[int]:
    if len(data) * 8 < count * width:
        raise ValueError("位域数据不足")
    packed = int.from_bytes(data, "little")
    return [(packed >> (i * width)) & ((1 << width) - 1) for i in range(count)]


def float32_seconds(data: bytes) -> float:
    require_size(data, 4, "浮点时间")
    value = struct.unpack("<f", data)[0]
    if not math.isfinite(value):
        raise ValueError("浮点时间不是有限数")
    return value


def pairing_fields(data: bytes, offer: bool) -> dict:
    require_size(data, 24 if offer else 4, "配对载荷")
    result = {"kind": "pairing_offer" if offer else "pairing_ack",
              "filter_address": f"0x{int.from_bytes(data[:4], 'little'):08X}"}
    if offer:
        result["channel_indices"] = list(data[4:])
    return result


def base_result(protocol: str, compact: CompactFrame, direction: str, name: str) -> dict:
    return {
        "protocol": protocol,
        "direction": direction,
        "direction_basis": "firmware_inferred" if direction != "unknown" else "unknown",
        "command": compact.command,
        "command_hex": f"0x{compact.command:02X}",
        "command_name": name,
        "sequence": compact.sequence,
        "need_ack": compact.need_ack,
        "is_ack": compact.is_ack,
        "payload_length": len(compact.data),
        "payload_hex": compact.data.hex(),
        "sum8_ok": True,
    }
