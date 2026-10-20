"""USRP 硬件射频与流式参数配置数据结构。"""
from dataclasses import asdict, dataclass
from math import isfinite


FORMAT_CPU_MAP = {
    "sc8": "sc8",
    "cs8": "sc8",
    "fc32": "fc32",
    "cf32": "fc32",
    "sc16": "sc16",
    "cs16": "sc16",
}

FORMAT_BYTES_PER_SAMPLE = {
    "sc8": 2,    # 1 byte I + 1 byte Q
    "cs8": 2,
    "sc16": 4,   # 2 bytes I + 2 bytes Q
    "cs16": 4,
    "fc32": 8,   # 4 bytes float I + 4 bytes float Q
    "cf32": 8,
}


@dataclass(frozen=True)
class USRPConfig:
    frequency_hz: float
    sample_rate: float = 2_000_000.0
    gain: float = 30.0
    channel: int = 0
    antenna: str | None = None
    bandwidth_hz: float | None = None
    device_args: str = ""
    clock_source: str | None = None
    time_source: str | None = None
    subdev: str | None = None
    cpu_format: str = "sc8"
    otw_format: str = "sc8"

    def __post_init__(self):
        if not isfinite(self.frequency_hz) or self.frequency_hz <= 0:
            raise ValueError("frequency_hz 必须为正有限数")
        if not isfinite(self.sample_rate) or self.sample_rate <= 0:
            raise ValueError("sample_rate 必须为正有限数")
        if not isfinite(self.gain) or self.gain < 0:
            raise ValueError("gain 必须为非负有限数")
        if not isinstance(self.channel, int) or isinstance(self.channel, bool) or self.channel < 0:
            raise ValueError("channel 必须为非负整数")
        if self.bandwidth_hz is not None and (not isfinite(self.bandwidth_hz) or self.bandwidth_hz <= 0):
            raise ValueError("bandwidth_hz 必须为正有限数")

        normalized_cpu = FORMAT_CPU_MAP.get(self.cpu_format.lower())
        if not normalized_cpu:
            raise ValueError(f"不支持的 cpu_format: {self.cpu_format}，可选: sc8/cs8, sc16, fc32/cf32")
        object.__setattr__(self, "cpu_format", normalized_cpu)

    @property
    def bytes_per_sample(self) -> int:
        return FORMAT_BYTES_PER_SAMPLE[self.cpu_format]

    def to_dict(self) -> dict:
        return asdict(self)
