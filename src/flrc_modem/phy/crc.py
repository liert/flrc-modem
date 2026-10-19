"""FLRC CRC24；范围包含同步字、硬件头及硬件载荷。"""


def crc24(data: bytes, initial: int = 0xFFFFFF) -> int:
    state = initial
    for byte in data:
        state ^= byte << 16
        for _ in range(8):
            state = ((state << 1) ^ (0x5D6DCB if state & 0x800000 else 0)) & 0xFFFFFF
    return state
