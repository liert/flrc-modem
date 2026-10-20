"""SX1280 FLRC 成包参数；长度候选和 CRC 初值不依赖厂商业务协议。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PacketFormat:
    header_type: str = "variable"
    payload_lengths: tuple[int, ...] = ()
    crc_bytes: int = 3
    crc_initial: int | None = None
    crc_initials: tuple[int, ...] = ()

    def __post_init__(self):
        if self.header_type not in ("variable", "fixed", "auto"):
            raise ValueError("header_type 必须为 variable、fixed 或 auto")
        if type(self.crc_bytes) is not int or self.crc_bytes not in (2, 3, 4):
            raise ValueError("crc_bytes 必须为 2、3 或 4；不支持无 CRC 的帧")
        for size in self.payload_lengths:
            if type(size) is not int or not 6 <= size <= 127:
                raise ValueError("payload_lengths 必须是 6..127B 的整数列表")
        object.__setattr__(self, "payload_lengths", tuple(dict.fromkeys(self.payload_lengths)))
        maximum = (1 << (8 * self.crc_bytes)) - 1
        if self.crc_initial is None:
            object.__setattr__(self, "crc_initial", maximum)
        seeds = tuple(dict.fromkeys(self.crc_initials))
        for seed in (self.crc_initial, *seeds):
            if type(seed) is not int or not 0 <= seed <= maximum:
                raise ValueError(f"CRC{8 * self.crc_bytes} 初值必须为 0..0x{maximum:X}")
        object.__setattr__(self, "crc_initials", seeds)

    @property
    def crc_seeds(self) -> tuple[int, ...]:
        # 显式候选列表替代单一初值；不按差分掩码猜测发送方向。
        return self.crc_initials or (self.crc_initial,)

    @property
    def fixed_lengths(self):
        # 未知固定包长时可逐一尝试，只有 CRC 命中才输出。
        return self.payload_lengths or range(6, 128)

    @property
    def maximum_data_bits(self) -> int:
        if self.header_type == "fixed":
            return 8 * (max(self.fixed_lengths) + self.crc_bytes)
        return 16 + 8 * (127 + self.crc_bytes)
