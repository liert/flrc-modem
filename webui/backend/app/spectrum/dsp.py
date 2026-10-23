"""SDR 实时频谱 DSP 核心计算模块：窗函数、双边 FFT、相干增益归一化、dBFS 计算及功率平均。"""
from __future__ import annotations

import math
from typing import Literal
import numpy as np

from flrc_modem.dsp.windows import get_window_and_coherent_gain
from flrc_modem.io.iq import decode_samples


def convert_raw_iq_to_complex(
    raw_bytes: bytes | memoryview | np.ndarray,
    iq_format: Literal["cs8", "sc16", "fc32"] = "cs8",
) -> np.ndarray:
    """使用核心 IQ 解码器，统一采样对完整性、归一化和有限值校验。"""
    return decode_samples(raw_bytes, iq_format)


def compute_spectrum(
    samples: np.ndarray,
    n_fft: int = 1024,
    sample_rate: float = 8000000.0,
    center_freq: float = 2480000000.0,
    window_name: str = "hann",
    previous_powers: np.ndarray | None = None,
    avg_weight: float = 0.0,
) -> dict:
    """对复数 IQ 样本执行加窗、双边 FFT、fftshift、相干增益归一化及可选指数平均。
    
    返回字典包含：
    - freq_min: 频谱起始 RF 频率 (Hz)
    - freq_max: 频谱截止 RF 频率 (Hz)
    - freq_step: RBW 分辨率 (Hz = sample_rate / n_fft)
    - powers: 长度为 n_fft 的功率数值列表 (单位: dBFS, 0 dBFS 代表满量程输入)
    - peak_freq: 频段内最高峰值点对应的 RF 频率 (Hz)
    - peak_power: 频段内最高峰值功率 (dBFS)
    """
    if n_fft < 256 or n_fft > 8192 or n_fft & (n_fft - 1):
        raise ValueError("FFT 点数必须为 256..8192 的 2 次幂")
    if not math.isfinite(sample_rate) or sample_rate <= 0 or not math.isfinite(center_freq):
        raise ValueError("采样率与中心频率必须有效且有限")
    if len(samples) < n_fft:
        raise ValueError(f"样本数量不足以计算 FFT: 长度={len(samples)}, 需要={n_fft}")

    # 取最新的 n_fft 个样本
    segment = samples[-n_fft:]
    window, coherent_gain = get_window_and_coherent_gain(n_fft, window_name)

    # 加窗与双边 FFT
    windowed = segment * window
    fft_raw = np.fft.fft(windowed, n=n_fft)
    fft_shifted = np.fft.fftshift(fft_raw)

    # 满量程相干增益归一化幅度
    magnitude = np.abs(fft_shifted) / coherent_gain
    eps = 1e-12
    power_dbfs = 20.0 * np.log10(np.maximum(magnitude, eps))

    # 指数功率平滑平均 (0 <= avg_weight < 1)
    if previous_powers is not None and len(previous_powers) == n_fft and 0.0 < avg_weight < 1.0:
        linear_power = (1.0 - avg_weight) * magnitude ** 2 + avg_weight * np.power(10.0, previous_powers / 10.0)
        power_dbfs = 10.0 * np.log10(np.maximum(linear_power, eps ** 2))

    # 频率轴参数
    rbw = sample_rate / n_fft
    freq_min = center_freq - sample_rate / 2.0
    freq_max = center_freq + sample_rate / 2.0 - rbw

    peak_idx = int(np.argmax(power_dbfs))
    peak_freq = float(freq_min + peak_idx * rbw)
    peak_power = float(power_dbfs[peak_idx])

    # 采用抛物线二次插值 (Parabolic Interpolation) 修正栅栏效应引起的功率谷损失与频偏微调
    if 0 < peak_idx < n_fft - 1:
        alpha = float(power_dbfs[peak_idx - 1])
        beta = float(power_dbfs[peak_idx])
        gamma = float(power_dbfs[peak_idx + 1])
        denom = alpha - 2.0 * beta + gamma
        if abs(denom) > 1e-5:
            delta = 0.5 * (alpha - gamma) / denom
            if abs(delta) <= 1.0:
                peak_freq = float(freq_min + (peak_idx + delta) * rbw)
                peak_power = float(beta - 0.25 * (alpha - gamma) * delta)

    # 适度保留精度 (保留 1 位小数即可满足仪器级显示，同时大幅压缩 WebSocket 载荷)
    powers_rounded = [round(float(p), 1) for p in power_dbfs]

    return {
        "center_freq": float(center_freq),
        "sample_rate": float(sample_rate),
        "n_fft": int(n_fft),
        "rbw_hz": float(rbw),
        "freq_min": float(freq_min),
        "freq_max": float(freq_max),
        "powers": powers_rounded,
        "peak_freq": float(peak_freq),
        "peak_power": round(peak_power, 1),
    }


def generate_test_tone_iq(
    sample_rate: float,
    num_samples: int,
    tone_offset_hz: float,
    amplitude_dbfs: float = 0.0,
    noise_floor_dbfs: float = -90.0,
    seed: int | None = 42,
) -> np.ndarray:
    """生成仿真单音复数 IQ 信号，用于纯软件环境下的自动化验证与算法测试。
    
    - tone_offset_hz: 相对于中心频率的频偏 (可为正、负或 0)
    - amplitude_dbfs: 单音幅度，0 dBFS 对应峰值 1.0
    - noise_floor_dbfs: 白噪声基底
    """
    rng = np.random.default_rng(seed)
    t = np.arange(num_samples, dtype=np.float64) / sample_rate

    # 计算目标幅度
    amp = 10.0 ** (amplitude_dbfs / 20.0)
    phase = 2.0 * np.pi * tone_offset_hz * t
    tone = amp * (np.cos(phase) + 1j * np.sin(phase))

    # 添加复高斯白噪声
    noise_amp = 10.0 ** (noise_floor_dbfs / 20.0)
    noise_i = rng.normal(0.0, noise_amp / math.sqrt(2.0), num_samples)
    noise_q = rng.normal(0.0, noise_amp / math.sqrt(2.0), num_samples)
    noise = noise_i + 1j * noise_q

    iq = tone + noise
    return iq.astype(np.complex64)
