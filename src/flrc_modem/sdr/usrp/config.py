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

# USRP N310 硬件原生支持的三组主时钟频率 (Master Clock Rate, MCR)
N310_SUPPORTED_MCRS = (122_880_000.0, 125_000_000.0, 153_600_000.0)
DEFAULT_N310_MCR = 125_000_000.0

# 在默认 125MHz MCR 下，N310 推荐整除采样率（50分频 = 2.5MSps）
DEFAULT_N310_SAMPLE_RATE = 2_500_000.0


def check_mcr_sample_rate_compatibility(
    mcr: float, sample_rate: float
) -> tuple[bool, int, float, str]:
    """检查采样率是否为 MCR 的整除因子。

    返回:
        (is_integer, decimation_factor, coerced_rate, advice_message)
    """
    if mcr <= 0 or sample_rate <= 0:
        return False, 0, sample_rate, "MCR 与采样率必须为正数"

    factor_exact = mcr / sample_rate
    nearest_factor = max(1, int(round(factor_exact)))
    coerced_rate = mcr / nearest_factor
    is_integer = abs(factor_exact - nearest_factor) < 1e-4

    if is_integer:
        if nearest_factor % 2 == 1:
            msg = (
                f"采样率 {sample_rate:g}Sps 是 MCR={mcr:g}Hz 的奇数分频 ({nearest_factor})。"
                f"N310 推荐使用偶数分频以获得最佳通带平坦度。"
            )
        else:
            msg = f"采样率 {sample_rate:g}Sps 为 MCR={mcr:g}Hz 的完美偶数整除分频 ({nearest_factor}分频)。"
        return True, nearest_factor, coerced_rate, msg

    # 非整除情况：分析推荐替代速率
    # 推荐最接近且为偶数分频的采样率
    recommended_even_factors = [f for f in (nearest_factor - 1, nearest_factor + 1, 50, 64) if f > 0 and f % 2 == 0]
    rec_text = ", ".join(f"{mcr / f:g}Sps ({f}分频)" for f in recommended_even_factors[:2])
    msg = (
        f"采样率 {sample_rate:g}Sps 无法整除 MCR={mcr:g}Hz (分频比为 {factor_exact:.3f})。"
        f"UHD 将强制对齐(Coerce)至 {coerced_rate:g}Sps ({nearest_factor}分频)。"
        f"推荐整除速率: {rec_text}。"
    )
    return False, nearest_factor, coerced_rate, msg


@dataclass(frozen=True)
class USRPConfig:
    frequency_hz: float
    sample_rate: float = DEFAULT_N310_SAMPLE_RATE
    gain: float = 30.0
    channel: int = 0
    antenna: str | None = None
    bandwidth_hz: float | None = None
    device_args: str = ""
    clock_source: str | None = None
    time_source: str | None = None
    subdev: str | None = None
    master_clock_rate: float | None = None
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
        if self.master_clock_rate is not None and (not isfinite(self.master_clock_rate) or self.master_clock_rate <= 0):
            raise ValueError("master_clock_rate 必须为正有限数")

        normalized_cpu = FORMAT_CPU_MAP.get(self.cpu_format.lower())
        if not normalized_cpu:
            raise ValueError(f"不支持的 cpu_format: {self.cpu_format}，可选: sc8/cs8, sc16, fc32/cf32")
        object.__setattr__(self, "cpu_format", normalized_cpu)

    @property
    def bytes_per_sample(self) -> int:
        return FORMAT_BYTES_PER_SAMPLE[self.cpu_format]

    def to_dict(self) -> dict:
        return asdict(self)
