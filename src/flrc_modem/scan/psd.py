"""频谱功率谱密度计算、自适应噪声基底估计与峰值聚类感知算法。"""
from __future__ import annotations

import math
import numpy as np

from ..dsp.windows import get_window_and_coherent_gain


def _fft_hop(n_fft, hop_size):
    if type(n_fft) is not int or n_fft < 16 or n_fft & (n_fft - 1):
        raise ValueError("FFT 点数必须是不小于 16 的 2 次幂")
    hop = n_fft // 2 if hop_size is None else hop_size
    if type(hop) is not int or not 1 <= hop <= n_fft:
        raise ValueError("FFT 步长必须为 1..n_fft 的整数")
    return hop


def iter_fft_powers(samples, n_fft=2048, hop_size=None, check_cancelled=None, window_name="hann"):
    """连续覆盖全部样本；固定批量 FFT，不因瀑布图行数限制跳过短突发。"""
    hop = _fft_hop(n_fft, hop_size)
    if len(samples) < n_fft:
        samples = np.pad(samples, (0, n_fft - len(samples)))
    views = np.lib.stride_tricks.sliding_window_view(samples, n_fft)[::hop]
    window, gain = get_window_and_coherent_gain(n_fft, window_name)
    for start in range(0, len(views), 128):
        if check_cancelled is not None:
            check_cancelled()
        fft = np.fft.fftshift(np.fft.fft(views[start:start + 128] * window, axis=1), axes=1)
        yield (np.abs(fft) / gain) ** 2
    # 尾窗回贴到最后一个完整窗口，避免强载波在零填充接缝处制造宽带假信号。
    if (len(samples) - n_fft) % hop:
        if check_cancelled is not None:
            check_cancelled()
        fft = np.fft.fftshift(np.fft.fft(samples[-n_fft:] * window))
        yield ((np.abs(fft) / gain) ** 2)[None, :]


def compute_psd_waterfall(
    samples: np.ndarray, n_fft: int = 2048, sample_rate: float = 10_000_000.0,
    center_freq: float = 2_480_000_000.0, hop_size: int | None = None,
    window_name: str = "hann", max_slices: int = 512, check_cancelled=None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """检测用最大谱和平均谱覆盖所有 FFT；瀑布图只压缩展示行，不丢检测窗口。"""
    if not math.isfinite(sample_rate) or sample_rate <= 0 or not math.isfinite(center_freq):
        raise ValueError("采样率与中心频率必须有效且有限")
    if type(max_slices) is not int or max_slices < 1:
        raise ValueError("max_slices 必须为正整数")
    hop = _fft_hop(n_fft, hop_size)
    count = 1 + math.ceil(max(0, len(samples) - n_fft) / hop)
    rows = min(count, max_slices)
    waterfall = np.zeros((rows, n_fft), dtype=np.float64)
    maximum = np.zeros(n_fft)
    total = np.zeros(n_fft)
    processed = 0
    for powers in iter_fft_powers(samples, n_fft, hop_size, check_cancelled, window_name):
        maximum = np.maximum(maximum, np.max(powers, axis=0))
        total += np.sum(powers, axis=0)
        buckets = np.arange(processed, processed + len(powers)) * rows // count
        np.maximum.at(waterfall, buckets, powers)
        processed += len(powers)
    rbw = sample_rate / n_fft
    freqs = center_freq - sample_rate / 2 + np.arange(n_fft) * rbw
    to_db = lambda values: 10 * np.log10(np.maximum(values, 1e-24))
    return freqs, to_db(maximum), to_db(total / processed), to_db(waterfall).astype(np.float32), rbw


def estimate_adaptive_noise_floor(
    avg_psd: np.ndarray,
    percentile: float = 25.0,
    edge_margin_ratio: float = 0.05,
    dc_margin_bins: int = 3,
) -> float:
    """自适应估计频段底噪基底 (dBFS)。
    
    使用低分位数 (默认 25th 百分位) 滤除活跃信号与突发载波，
    同时剔除模拟抗混叠滤波器边沿滚降与零中频 DC 泄漏尖峰。
    """
    n_fft = len(avg_psd)
    edge_bins = max(1, int(n_fft * edge_margin_ratio))
    center_bin = n_fft // 2

    # 创建掩码剔除边缘和 DC
    mask = np.ones(n_fft, dtype=bool)
    mask[:edge_bins] = False
    mask[-edge_bins:] = False
    mask[max(0, center_bin - dc_margin_bins) : min(n_fft, center_bin + dc_margin_bins + 1)] = False

    valid_bins = avg_psd[mask]
    if len(valid_bins) == 0:
        valid_bins = avg_psd

    noise_floor = float(np.percentile(valid_bins, percentile))
    return noise_floor


def local_noise_floor(avg_psd, rbw):
    """训练单元避开待测单元和保护区，用低分位数抵抗邻频强载波。"""
    from scipy.ndimage import percentile_filter
    radius = max(8, min(len(avg_psd) // 2 - 1, int(1_000_000 / rbw)))
    guard = min(radius - 1, max(2, int(75_000 / rbw)))
    footprint = np.ones(2 * radius + 1, dtype=bool)
    footprint[radius - guard:radius + guard + 1] = False
    return percentile_filter(avg_psd, percentile=25, footprint=footprint, mode="reflect")


def detect_spectral_peaks(
    freqs: np.ndarray, max_psd: np.ndarray, avg_psd: np.ndarray,
    noise_floor_dbfs: float, threshold_db: float = 10.0,
    min_bandwidth_hz: float = 40_000.0, dc_center_freq: float | None = None,
    time_slices: np.ndarray | None = None, edge_margin_ratio: float = 0.04,
    samples: np.ndarray | None = None, check_cancelled=None,
) -> list[dict]:
    """局部门限找候选，再按连续 FFT 的活跃突发估计中心频率、带宽与占空比。"""
    n_fft = len(freqs)
    rbw = float(freqs[1] - freqs[0])
    noise_db = local_noise_floor(avg_psd, rbw)
    frame_count = (1 + math.ceil(max(0, len(samples) - n_fft) / (n_fft // 2))
                   if samples is not None else len(time_slices) if time_slices is not None else 1)
    # 最大谱的噪声极值随观察时间上升，按窗口数补偿；不改变用户指定的更高门限。
    detection_db = max(threshold_db, 10 * math.log10(math.log(max(1, frame_count) / .01)))
    active = max_psd > noise_db + detection_db
    edge = max(1, int(n_fft * edge_margin_ratio))
    active[:edge] = active[-edge:] = False
    if dc_center_freq is not None:
        dc = int(np.argmin(np.abs(freqs - dc_center_freq)))
        lo, hi = max(0, dc - 2), min(n_fft, dc + 3)
        if np.count_nonzero(active[lo:hi]) <= 2:
            active[lo:hi] = False
    changes = np.diff(np.r_[False, active, False].astype(np.int8))
    regions = list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))

    def energy_width(lo, hi):
        power = np.power(10.0, max_psd[lo:hi] / 10)
        cumulative = np.cumsum(power) / np.sum(power)
        left, right = np.searchsorted(cumulative, [.01, .99])
        return (right - left + 1) * rbw

    merged = []
    for lo, hi in regions:
        merged.append((lo, hi))
        # 小碎片并入宽边带后，需再检查前一个区域，避免碎片阻断同一调制谱。
        while len(merged) > 1:
            prev_lo, prev_hi = merged[-2]
            lo, hi = merged[-1]
            gap = lo - prev_hi
            # 宽的调制边带可以合并；两个窄 CW 不能仅因落在 150kHz 内就合成一个信道。
            allowed = min(int(150_000 / rbw), int(1.5 * ((prev_hi - prev_lo) + (hi - lo))))
            # 强 CW 的窗函数旁瓣可超过门限数十个 bin，按能量宽度识别窄载波。
            broad = min(energy_width(prev_lo, prev_hi), energy_width(lo, hi)) >= min_bandwidth_hz
            if gap <= max(2, int(5000 / rbw)) or (broad and gap <= allowed):
                merged[-2:] = [(prev_lo, hi)]
            else:
                break
    if not merged:
        return []
    noise = np.power(10.0, noise_db / 10)
    profiles = [np.zeros(hi - lo) for lo, hi in merged]
    occupied = [[] for _ in merged]
    if samples is not None:
        batches = iter_fft_powers(samples, n_fft, check_cancelled=check_cancelled)
    else:
        batches = [np.power(10.0, (time_slices if time_slices is not None else max_psd[None, :]) / 10)]
    for powers in batches:
        for index, (lo, hi) in enumerate(merged):
            region = powers[:, lo:hi]
            # 积分能量检验避免把单个随机噪声尖峰解释成持续突发。
            present = np.mean(region, axis=1) > np.mean(noise[lo:hi]) * 10 ** (max(3.0, threshold_db - 6) / 10)
            occupied[index].extend(present.tolist())
            profiles[index] += np.sum(region[present], axis=0)
    candidates = []
    for (lo, hi), profile, flags in zip(merged, profiles, occupied):
        present = np.array(flags, dtype=bool)
        active_count = int(np.count_nonzero(present))
        if not active_count:
            continue
        # 填补一个 FFT 步长的小间断，减少同一突发被调制谷点拆开。
        if len(present) > 2:
            present[1:-1] |= present[:-2] & present[2:]
        burst_count = int(np.count_nonzero(present & ~np.r_[False, present[:-1]]))
        profile = np.maximum(profile / active_count - noise[lo:hi], 0)
        if np.sum(profile) <= 0:
            continue
        cumulative = np.cumsum(profile) / np.sum(profile)
        left, right = np.searchsorted(cumulative, [.01, .99])
        bw = max(float((right - left + 1) * rbw), rbw)
        center = float(np.sum(freqs[lo:hi] * profile) / np.sum(profile))
        peak_idx = lo + int(np.argmax(profile))
        peak_power = float(max_psd[peak_idx])
        local_floor = float(noise_db[peak_idx])
        snr = peak_power - local_floor
        if bw < min_bandwidth_hz and snr < 20:
            continue
        candidates.append({
            "frequency_hz": center, "peak_freq_hz": float(freqs[peak_idx]), "bandwidth_hz": bw,
            "peak_power_dbfs": round(peak_power, 1), "snr_db": round(snr, 1),
            "noise_floor_dbfs": round(local_floor, 1), "burst_count": burst_count,
            "active_slices": active_count, "total_slices": len(flags),
            "duty_cycle": round(active_count / len(flags), 3), "bin_range": (lo, hi),
            "frequency_resolution_hz": rbw, "frequency_method": "burst_centroid",
        })
    return candidates
