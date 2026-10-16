"""收发共用的 FLRC 参数取值，避免命令行与算法接受不同设置。"""
CODING_RATES = ("1/2", "3/4", "1")

# 键为原始空口 bit/s；值为芯片标称双边带宽 Hz、SetModulationParams 第一个参数。
# BW 是芯片档位，不作为额外的发射 IQ 砖墙低通滤波器。
FLRC_RATE_PROFILES = {
    260_000: (300_000, 0xEB),
    325_000: (300_000, 0xC7),
    520_000: (600_000, 0xAA),
    650_000: (600_000, 0x86),
    1_040_000: (1_200_000, 0x69),
    1_300_000: (1_200_000, 0x45),
}
RAMP_TIME_PARAMS = {2: 0x00, 4: 0x20, 6: 0x40, 8: 0x60,
                    10: 0x80, 12: 0xA0, 16: 0xC0, 20: 0xE0}


def resolve_bandwidth(bit_rate: float, bandwidth_hz: float | None = None) -> float:
    if bit_rate not in FLRC_RATE_PROFILES:
        raise ValueError("SX1280 FLRC bitrate 必须为 260000、325000、520000、650000、1040000 或 1300000")
    expected, _ = FLRC_RATE_PROFILES[bit_rate]
    if bandwidth_hz is not None and bandwidth_hz != expected:
        raise ValueError(f"bitrate={bit_rate:g} 必须配对 BW={expected}Hz")
    return float(expected)


def validate_coding_rate(value: str) -> str:
    if value not in CODING_RATES:
        raise ValueError("CR 必须为 1/2、3/4 或 1")
    return value


def parse_bt(value) -> float | str:
    """no 为关闭高斯成形；只接受芯片提供的三种模式。"""
    if isinstance(value, str) and value.lower() in ("no", "off"):
        return "no"
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("BT 必须为 no、0.5 或 1") from error
    if number not in (0.5, 1.0):
        raise ValueError("BT 必须为 no、0.5 或 1")
    return number


def tail_length(coding_rate: str) -> int:
    return 0 if validate_coding_rate(coding_rate) == "1" else 6
