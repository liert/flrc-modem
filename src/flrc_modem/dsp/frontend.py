"""下变频、抗混叠抽取、FIR 低通及粗频偏补偿。"""
from collections import OrderedDict
import numpy as np
from scipy.signal import firwin, oaconvolve, resample_poly

from ..config import DemodConfig


def _power_stats_kernel(filtered, gate_span: int):
    n = len(filtered)
    power = np.empty(n, dtype=np.float64)
    power_sum = np.empty(n + 1, dtype=np.float64)
    power_sum[0] = 0.0
    peak_power = 0.0
    acc = 0.0
    for i in range(n):
        v = filtered[i]
        vr = np.float64(v.real)
        vi = np.float64(v.imag)
        p = vr * vr + vi * vi
        power[i] = p
        acc += p
        power_sum[i + 1] = acc
        if p > peak_power:
            peak_power = p
    best_span = 0.0
    if n > gate_span:
        for i in range(n - gate_span + 1):
            sp = power_sum[i + gate_span] - power_sum[i]
            if sp > best_span:
                best_span = sp
    return power, power_sum, peak_power, best_span


def _cfo_angles_kernel(filtered, power, peak_power: float, cfo_lag: int, window: int, work_rate: float):
    """仅在强信号区间周围累积延迟自相关并求相位角，与全窗口平滑后按 strong 索引等价。"""
    n = len(filtered)
    m = n - cfo_lag
    threshold = peak_power * 0.4
    first_strong = -1
    last_strong = -1
    strong_count = 0
    for i in range(m):
        if power[i + cfo_lag] > threshold:
            if first_strong < 0:
                first_strong = i
            last_strong = i
            strong_count += 1
    if strong_count == 0:
        return np.empty(0, dtype=np.float64)
    pad_left = window // 2
    span_len = (last_strong - first_strong) + window
    sums = np.empty(span_len + 1, dtype=np.complex128)
    sums[0] = 0j
    acc = 0j
    for offset in range(span_len):
        p_idx = first_strong + offset - pad_left
        if 0 <= p_idx < m:
            a = filtered[p_idx + cfo_lag]
            b = filtered[p_idx]
            acc += complex(np.float64(a.real), np.float64(a.imag)) * complex(np.float64(b.real), -np.float64(b.imag))
        sums[offset + 1] = acc
    lag_seconds = cfo_lag / work_rate
    scale = 1.0 / (2.0 * np.pi * lag_seconds)
    angles = np.empty(strong_count, dtype=np.float64)
    k = 0
    for i in range(first_strong, last_strong + 1):
        if power[i + cfo_lag] > threshold:
            rel = i - first_strong
            sm = (sums[rel + window] - sums[rel]) / window
            angles[k] = np.arctan2(sm.imag, sm.real) * scale
            k += 1
    return angles


def _derotate_kernel(filtered, cfo: float, work_rate: float):
    """分块精确重置相位的旋转因子递推，将 4 万次复指数求值减为数百次且无累积漂移。"""
    n = len(filtered)
    out = np.empty(n, dtype=np.complex128)
    if cfo == 0.0:
        for i in range(n):
            v = filtered[i]
            out[i] = complex(np.float64(v.real), np.float64(v.imag))
        return out
    step = -2.0 * np.pi * cfo / work_rate
    d_re = np.cos(step)
    d_im = np.sin(step)
    for base in range(0, n, 128):
        ang = step * base
        r_re = np.cos(ang)
        r_im = np.sin(ang)
        end = min(n, base + 128)
        for i in range(base, end):
            v = filtered[i]
            vr = np.float64(v.real)
            vi = np.float64(v.imag)
            out[i] = complex(vr * r_re - vi * r_im, vr * r_im + vi * r_re)
            nr = r_re * d_re - r_im * d_im
            r_im = r_re * d_im + r_im * d_re
            r_re = nr
    return out


try:
    from numba import njit
except ImportError:
    _jit_power_stats = None
    _jit_cfo_angles = None
    _jit_derotate = None
else:
    _jit_power_stats = njit(cache=True, nogil=True)(_power_stats_kernel)
    _jit_cfo_angles = njit(cache=True, nogil=True)(_cfo_angles_kernel)
    _jit_derotate = njit(cache=True, nogil=True)(_derotate_kernel)


class Frontend:
    def __init__(self, config: DemodConfig):
        self.config = config
        self.sps = config.work_rate / config.bit_rate
        self.cfo_lag = int(round(2 * self.sps))
        self._gate_span = max(1, int(32 * self.sps))
        self.filter_delay = (config.filter_taps - 1) / 2
        self.taps = firwin(config.filter_taps, config.filter_cutoff,
                           fs=config.work_rate, window="hamming").astype(np.float32)
        self._oscillators = OrderedDict()
        self._oscillator_bytes = 0
        self._last_power = None
        self._last_power_sum = None

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

    def _has_strong_span(self, power, background) -> bool:
        """与检测器 32 符号局部功率门限同口径的预筛。

        逐点峰值/中位数对纯噪声几乎总能超过门限（约 15 倍），使噪声窗口也走完 CFO 估计和
        多相位搜索；平滑后的峰值才与下游 strong 判据一致。下游跨度可能多 1 个采样，
        留 10% 余量，只拒绝下游必然拒绝的窗口。
        """
        span = self._gate_span
        if len(power) <= span:
            return True
        sums = np.concatenate(([0.0], np.cumsum(power, dtype=np.float64)))
        peak = float(np.max(sums[span:] - sums[:-span])) / span
        return peak >= 0.9 * self.config.min_peak_ratio * background

    def process(self, iq, offset_hz: float):
        config = self.config
        if len(iq) < max(256, int(120 * config.sample_rate / config.bit_rate)):
            return None
        # offset_hz == 0 时无需生成全 1 复振荡器并升至 complex128 相乘，数学完全等价。
        if offset_hz == 0.0:
            baseband = (iq - np.mean(iq)).astype(np.complex64)
        else:
            # 采用双精度生成振荡器相位，防止长窗口产生 float32 相位舍入误差。
            oscillator = self._oscillator(len(iq), offset_hz)
            baseband = ((iq - np.mean(iq)) * oscillator).astype(np.complex64)
        if config.decimation > 1:
            baseband = resample_poly(baseband, 1, config.decimation).astype(np.complex64, copy=False)
        # 因果 FIR 低通等价于 full 卷积的前 N 点；overlap-add FFT 将 101 抽头滤波缩短约 4 倍。
        filtered = oaconvolve(baseband, self.taps)[:len(baseband)]
        window = max(1, int(round(16 * self.sps)))
        lag_seconds = self.cfo_lag / config.work_rate

        if _jit_power_stats is not None:
            power, power_sum, peak_power, best_span = _jit_power_stats(filtered, self._gate_span)
            background = max(float(np.median(power)), np.finfo(float).tiny)
            if peak_power <= 0 or peak_power / background < config.min_peak_ratio:
                return None
            if len(power) > self._gate_span and best_span / self._gate_span < 0.9 * config.min_peak_ratio * background:
                return None
            angles = _jit_cfo_angles(filtered, power, peak_power, self.cfo_lag, window, config.work_rate)
            cfo = float(np.median(angles)) if len(angles) else 0.0
            if abs(cfo) >= 1 / (2 * lag_seconds):
                cfo = 0.0
            filtered = _jit_derotate(filtered, cfo, config.work_rate)
            self._last_power = power
            self._last_power_sum = power_sum
            return filtered, power, background, cfo

        filtered = filtered.astype(np.complex128)
        power = np.abs(filtered) ** 2
        peak_power = float(np.max(power))
        background = max(float(np.median(power)), np.finfo(float).tiny)
        if peak_power <= 0 or peak_power / background < config.min_peak_ratio or not self._has_strong_span(power, background):
            return None
        # 两符号延迟消除交替前导中的调制分量，估计窗口粗 CFO。
        product = filtered[self.cfo_lag:] * np.conj(filtered[:-self.cfo_lag])
        # 等价于矩形核的 same 卷积，前缀和将开销从 O(N*window) 降到 O(N)。
        # 偶数核左右补零不对称，保持与 np.convolve 的中心位置一致。
        padded = np.pad(product, (window // 2, (window - 1) // 2))
        sums = np.concatenate(([0j], np.cumsum(padded, dtype=np.complex128)))
        smoothed = (sums[window:] - sums[:-window]) / window
        cfo_series = np.angle(smoothed) / (2 * np.pi * lag_seconds)
        strong = np.flatnonzero(power[self.cfo_lag:] > peak_power * 0.4)
        cfo = float(np.median(cfo_series[strong])) if len(strong) else 0.0
        if abs(cfo) >= 1 / (2 * lag_seconds):
            cfo = 0.0
        time = np.arange(len(filtered), dtype=np.float64) / config.work_rate
        filtered *= np.exp(-2j * np.pi * cfo * time)
        return filtered, power, background, cfo
