"""配置读取和边界检查，不包含任何设备的业务协议字段。"""
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import tomllib

from .phy.fec import coded_length
from .phy.profile import parse_bt, tail_length
from .phy.packet_format import PacketFormat


@dataclass(frozen=True)
class DemodConfig:
    sample_rate: float = 10_000_000.0
    center_frequency: float = 2_480_000_000.0
    frequencies: tuple[float, ...] = (2_480_060_000.0,)
    bit_rate: float = 325_000.0
    coding_rate: str = "3/4"
    bt: float | str = 1.0
    iq_format: str = "cs8"
    chunk_seconds: float = 0.1
    overlap_seconds: float | None = None
    max_seconds: float = 0.0
    filter_cutoff: float = 350_000.0
    filter_taps: int = 101
    min_peak_ratio: float = 3.0
    max_preamble_errors: int = 1
    max_coded_bits: int | None = None
    crc_initial: int | None = None
    sync_word: bytes | None = None
    header_type: str = "variable"
    payload_lengths: tuple[int, ...] = ()
    crc_bytes: int = 3
    crc_initials: tuple[int, ...] = ()
    allow_frequency_wrap: bool = False

    def __post_init__(self):
        object.__setattr__(self, "bt", parse_bt(self.bt))
        packet_format = self.packet_format
        object.__setattr__(self, "crc_initial", packet_format.crc_initial)
        object.__setattr__(self, "crc_initials", packet_format.crc_initials)
        object.__setattr__(self, "payload_lengths", packet_format.payload_lengths)
        maximum_bits = coded_length(packet_format.maximum_data_bits + tail_length(self.coding_rate), self.coding_rate)
        if self.max_coded_bits is None:
            object.__setattr__(self, "max_coded_bits", max(1500, maximum_bits))
        for name in ("sample_rate", "center_frequency", "bit_rate", "chunk_seconds", "filter_cutoff", "min_peak_ratio"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        minimum_overlap = (85 + maximum_bits) / self.bit_rate + self.filter_taps / self.work_rate
        if self.overlap_seconds is None:
            object.__setattr__(self, "overlap_seconds", max(0.006, minimum_overlap + 0.0001))
        for name in ("overlap_seconds", "max_seconds"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须是有限非负数")
        if not self.frequencies or any(not isfinite(f) or f <= 0 for f in self.frequencies):
            raise ValueError("至少指定一个有效的目标频率")
        if type(self.allow_frequency_wrap) is not bool:
            raise ValueError("allow_frequency_wrap 必须为布尔值")
        for frequency in self.frequencies:
            offset = abs(frequency - self.center_frequency)
            if offset >= self.sample_rate / 2 or (not self.allow_frequency_wrap and offset + self.filter_cutoff >= self.sample_rate / 2):
                raise ValueError("目标频率及滤波通带必须位于采样带内")
        if self.iq_format not in ("cs8", "cu8", "cf32"):
            raise ValueError("IQ 格式必须是 cs8、cu8 或 cf32")
        if self.filter_taps < 3 or self.filter_taps % 2 == 0:
            raise ValueError("FIR 抽头数必须为不小于 3 的奇数")
        if not 0 <= self.max_preamble_errors <= 21:
            raise ValueError("定时前导允许误码数必须为 0..21")
        if self.max_coded_bits < maximum_bits:
            raise ValueError(f"CR={self.coding_rate} 编码窗口至少需要 {maximum_bits}bit，以覆盖配置的最大物理载荷")
        if self.sync_word is not None and len(self.sync_word) != 4:
            raise ValueError("同步字必须是 4B")
        # 分块重叠覆盖完整最长包；防止跨窗口的包被静默截断。
        if self.overlap_seconds < minimum_overlap:
            raise ValueError(f"重叠时间至少应为 {minimum_overlap * 1000:.3f}ms")
        if self.work_rate < 4 * self.bit_rate:
            raise ValueError("输入采样率不足以提供每符号至少 4 个处理采样点")
        if self.filter_cutoff >= self.work_rate / 2:
            raise ValueError("低通截止频率超过处理采样率的 Nyquist 范围")

    @property
    def packet_format(self) -> PacketFormat:
        return PacketFormat(self.header_type, self.payload_lengths, self.crc_bytes,
                            self.crc_initial, self.crc_initials)

    @property
    def decimation(self) -> int:
        return max(1, int(self.sample_rate // max(2_000_000.0, 4 * self.bit_rate)))

    @property
    def work_rate(self) -> float:
        return self.sample_rate / self.decimation


def load_config(path: Path | None, overrides: dict) -> DemodConfig:
    values = {}
    if path is not None:
        with path.open("rb") as stream:
            values = tomllib.load(stream).get("demod", {})
    # 未给出的 CLI 参数保持 None，防止默认值覆盖配置文件。
    if overrides.get("crc_initial") is not None and overrides.get("crc_initials") is None:
        values.pop("crc_initials", None)
    values.update({name: value for name, value in overrides.items() if value is not None})
    if "frequencies" in values:
        values["frequencies"] = tuple(float(f) for f in values["frequencies"])
    if isinstance(values.get("sync_word"), str):
        values["sync_word"] = bytes.fromhex(values["sync_word"])
    unknown = set(values) - DemodConfig.__dataclass_fields__.keys()
    if unknown:
        raise ValueError(f"未知配置项：{', '.join(sorted(unknown))}")
    return DemodConfig(**values)
