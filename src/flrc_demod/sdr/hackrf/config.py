"""HackRF 模拟基带带宽与 SX1280 FLRC 标称 BW 是不同参数。"""
from dataclasses import dataclass
from math import isfinite

BASEBAND_BANDWIDTHS = (1_750_000, 2_500_000, 3_500_000, 5_000_000, 5_500_000,
                       6_000_000, 7_000_000, 8_000_000, 9_000_000, 10_000_000,
                       12_000_000, 14_000_000, 15_000_000, 20_000_000, 24_000_000, 28_000_000)


@dataclass(frozen=True)
class HackRFConfig:
    frequency_hz: int
    sample_rate: float = 10_000_000
    bandwidth_hz: int | None = None
    tx_gain: int = 0
    lna_gain: int = 16
    vga_gain: int = 16
    amp: bool = False
    bias_tee: bool = False

    def __post_init__(self):
        if not isinstance(self.frequency_hz, int) or not 1_000_000 <= self.frequency_hz <= 6_000_000_000:
            raise ValueError("HackRF 频率必须为 1MHz..6GHz 的整数 Hz")
        if not isfinite(self.sample_rate) or not 2_000_000 <= self.sample_rate <= 20_000_000:
            raise ValueError("HackRF 采样率必须为 2..20MSps")
        if self.bandwidth_hz is None:
            choices = [value for value in BASEBAND_BANDWIDTHS if value <= self.sample_rate * 0.75]
            object.__setattr__(self, "bandwidth_hz", max(choices) if choices else BASEBAND_BANDWIDTHS[0])
        if self.bandwidth_hz not in BASEBAND_BANDWIDTHS:
            raise ValueError("不支持的 HackRF 模拟基带带宽")
        for name, maximum, step in (("tx_gain", 47, 1), ("lna_gain", 40, 8), ("vga_gain", 62, 2)):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= maximum or value % step:
                raise ValueError(f"{name} 必须为 0..{maximum}、步长 {step} 的整数")
