"""信号生成参数；频率偏移相对于 IQ 文件中心。"""
from dataclasses import dataclass
from math import isfinite

from ..phy.profile import RAMP_TIME_PARAMS, parse_bt, resolve_bandwidth, validate_coding_rate


@dataclass(frozen=True)
class ModulationConfig:
    sample_rate: float = 10_000_000.0
    bit_rate: float = 325_000.0
    bandwidth_hz: float | None = None
    ramp_time_us: float = 12.0
    offset_hz: float = 60_000.0
    bt: float | str = 1.0
    coding_rate: str = "3/4"
    amplitude: float = 0.7
    repeat: int = 1
    gap_seconds: float = 0.010
    guard_seconds: float = 0.010
    iq_format: str = "cs8"

    def __post_init__(self):
        object.__setattr__(self, "bt", parse_bt(self.bt))
        validate_coding_rate(self.coding_rate)
        for name in ("sample_rate", "bit_rate"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        object.__setattr__(self, "bandwidth_hz", resolve_bandwidth(self.bit_rate, self.bandwidth_hz))
        if self.ramp_time_us not in RAMP_TIME_PARAMS:
            raise ValueError("RampTime 必须为 2、4、6、8、10、12、16 或 20µs")
        if self.sample_rate < 4 * self.bit_rate:
            raise ValueError("调制至少需要每比特 4 个采样点")
        if not isfinite(self.offset_hz) or abs(self.offset_hz) + self.bit_rate >= self.sample_rate / 2:
            raise ValueError("频率偏移及调制带宽必须位于采样带内")
        if not isfinite(self.amplitude) or not 0 < self.amplitude <= 1:
            raise ValueError("幅度必须为 (0, 1] 内的有限数")
        if not isinstance(self.repeat, int) or self.repeat < 1:
            raise ValueError("重复次数必须为正整数")
        for name in ("gap_seconds", "guard_seconds"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须是有限非负数")
        if self.iq_format not in ("cs8", "cu8", "cf32"):
            raise ValueError("IQ 格式必须是 cs8、cu8 或 cf32")

    @property
    def deviation_hz(self) -> float:
        # h=0.5 的二进制连续相位模型：单侧频偏为码率的四分之一。
        return self.bit_rate / 4

    @property
    def ramp_samples(self) -> int:
        # 以最近的整数采样间隔实现指定时间；元数据同时记录实际间隔数。
        return max(1, round(self.sample_rate * self.ramp_time_us / 1_000_000))
