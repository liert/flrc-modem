"""下变频、抗混叠抽取、FIR 低通及粗频偏补偿。"""
from collections import OrderedDict
import numpy as np
from scipy.signal import firwin, lfilter, resample_poly

from ..config import DemodConfig


class Frontend:
    def __init__(self, config: DemodConfig):
        self.config = config
        self.sps = config.work_rate / config.bit_rate
        self.cfo_lag = int(round(2 * self.sps))
        self.filter_delay = (config.filter_taps - 1) / 2
        self.taps = firwin(config.filter_taps, config.filter_cutoff,
                           fs=config.work_rate, window="hamming").astype(np.float32)
        self._oscillators = OrderedDict()
        self._oscillator_bytes = 0

    def _oscillator(self, count, offset_hz):
        key = (count, offset_hz)
        if key in self._oscillators:
            self._oscillators.move_to_end(key)
            return self._oscillators[key]
        sample_index = np.arange(count, dtype=np.float64)
        oscillator = np.exp(-2j * np.pi * offset_hz * sample_index / self.config.sample_rate)
        # 保留双精度相位，缓存常见长度的振荡器；大范围扫频也不无限积累内存。
        budget = 64 * 1024 * 1024
        if oscillator.nbytes <= budget:
            while self._oscillators and self._oscillator_bytes + oscillator.nbytes > budget:
                _, old = self._oscillators.popitem(last=False)
                self._oscillator_bytes -= old.nbytes
            self._oscillators[key] = oscillator
            self._oscillator_bytes += oscillator.nbytes
        return oscillator

    def process(self, iq, offset_hz: float):
        config = self.config
        if len(iq) < max(256, int(120 * config.sample_rate / config.bit_rate)):
            return None
        # 采用双精度生成振荡器相位，防止长窗口产生 float32 相位舍入误差。
        oscillator = self._oscillator(len(iq), offset_hz)
        baseband = ((iq - np.mean(iq)) * oscillator).astype(np.complex64)
        if config.decimation > 1:
            baseband = resample_poly(baseband, 1, config.decimation)
        filtered = lfilter(self.taps, 1.0, baseband)
        power = np.abs(filtered) ** 2
        peak_power = float(np.max(power))
        background = max(float(np.median(power)), np.finfo(float).tiny)
        if peak_power <= 0 or peak_power / background < config.min_peak_ratio:
            return None
        # 两符号延迟消除交替前导中的调制分量，估计窗口粗 CFO。
        product = filtered[self.cfo_lag:] * np.conj(filtered[:-self.cfo_lag])
        window = max(1, int(round(16 * self.sps)))
        # 等价于矩形核的 same 卷积，前缀和将开销从 O(N*window) 降到 O(N)。
        # 偶数核左右补零不对称，保持与 np.convolve 的中心位置一致。
        padded = np.pad(product, (window // 2, (window - 1) // 2))
        sums = np.concatenate(([0j], np.cumsum(padded, dtype=np.complex128)))
        smoothed = (sums[window:] - sums[:-window]) / window
        lag_seconds = self.cfo_lag / config.work_rate
        cfo_series = np.angle(smoothed) / (2 * np.pi * lag_seconds)
        strong = np.flatnonzero(power[self.cfo_lag:] > peak_power * 0.4)
        cfo = float(np.median(cfo_series[strong])) if len(strong) else 0.0
        if abs(cfo) >= 1 / (2 * lag_seconds):
            cfo = 0.0
        time = np.arange(len(filtered), dtype=np.float64) / config.work_rate
        filtered *= np.exp(-2j * np.pi * cfo * time)
        return filtered, power, background, cfo
