"""高斯频率成形、相位积分和数字上变频。"""
from math import ceil, pi
import numpy as np

from ..dsp.shaping import shape_frequency

from .config import ModulationConfig

MAX_BURST_SAMPLES = 5_000_000
SILENCE_CHUNK = 65_536


def modulate_bits(bits, config: ModulationConfig) -> np.ndarray:
    """输出归一化复数基带；支持非整数每比特采样数，h=0.5。"""
    bits = np.asarray(bits)
    if bits.ndim != 1 or not len(bits) or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("调制输入必须是非空一维 0/1 比特")
    sps = config.sample_rate / config.bit_rate
    body_count = ceil((len(bits) + 8) * sps)
    ramp = config.ramp_samples
    count = body_count + 2 * ramp
    if count > MAX_BURST_SAMPLES:
        raise ValueError(f"单个突发超过 {MAX_BURST_SAMPLES} 个采样点")
    indices = np.minimum((np.arange(body_count) / sps).astype(np.int64), len(bits) - 1)
    frequency = np.where(bits[indices] == 1, config.deviation_hz, -config.deviation_hz)
    # 前置爬升保持首符号，尾部保持末符号；不挤占完整前导和编码数据。
    frequency = np.pad(frequency, (ramp, ramp), mode="edge")
    frequency = shape_frequency(frequency, sps, config.bt)
    phase = 2 * pi * np.cumsum(frequency) / config.sample_rate
    waveform = (config.amplitude * np.exp(1j * phase)).astype(np.complex64)
    # 用 N 个采样间隔完成 0→1 和 1→0，完整数据区始终维持设定幅度。
    # 当前为线性 IQ 包络近似，不宣称复现芯片模拟 PA 的精确爬升曲线。
    envelope = np.arange(ramp, dtype=np.float64) / ramp
    waveform[:ramp] *= envelope
    waveform[-ramp:] *= envelope[::-1]
    return waveform


def _silence(count: int):
    while count > 0:
        size = min(count, SILENCE_CHUNK)
        yield np.zeros(size, dtype=np.complex64)
        count -= size


def iter_signal(bits, config: ModulationConfig):
    """逐块产生头尾静默、重复突发及间隙，避免按重复次数累积内存。"""
    waveform = modulate_bits(bits, config)
    guard = round(config.guard_seconds * config.sample_rate)
    gap = round(config.gap_seconds * config.sample_rate)
    yield from _silence(guard)
    start = guard
    for index in range(config.repeat):
        # 使用整个文件内的样本位置生成载波相位。
        positions = start + np.arange(len(waveform), dtype=np.float64)
        carrier = np.exp(2j * pi * config.offset_hz * positions / config.sample_rate)
        yield (waveform * carrier).astype(np.complex64)
        start += len(waveform)
        if index + 1 < config.repeat:
            yield from _silence(gap)
            start += gap
    yield from _silence(guard)
