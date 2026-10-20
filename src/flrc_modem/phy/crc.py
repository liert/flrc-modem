"""SX1280 FLRC CRC；MSB-first，无反射/末尾异或，覆盖同步字及包体。"""

POLYNOMIALS = {2: 0x755B, 3: 0x5D6DCB, 4: 0xF4ACFB13}


def flrc_crc(data: bytes, crc_bytes: int = 3, initial: int | None = None) -> int:
    if type(crc_bytes) is not int or crc_bytes not in POLYNOMIALS:
        raise ValueError("FLRC CRC 长度必须为 2、3 或 4B")
    width = crc_bytes * 8
    mask, top = (1 << width) - 1, 1 << (width - 1)
    state = mask if initial is None else initial
    if type(state) is not int or not 0 <= state <= mask:
        raise ValueError(f"CRC{width} 初值必须为 0..0x{mask:X}")
    polynomial = POLYNOMIALS[crc_bytes]
    for byte in data:
        state ^= byte << (width - 8)
        for _ in range(8):
            state = ((state << 1) ^ (polynomial if state & top else 0)) & mask
    return state


def crc24(data: bytes, initial: int = 0xFFFFFF) -> int:
    return flrc_crc(data, 3, initial)
