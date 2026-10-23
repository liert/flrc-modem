"""频谱功率谱密度计算、自适应噪声基底估计与峰值聚类感知算法。"""
from __future__ import annotations

import math
from typing import Literal
import numpy as np

from ..dsp.windows import get_window_and_coherent_gain


def compute_psd_waterfall(
    samples: np.ndarray,
    n_fft: int = 2048,
    sample_rate: float = 10_000_000.0,
    center_freq: float = 2_480_000_000.0,
    hop_size: int | None = None,
    window_name: str = "hann",
    max_slices: int = 512,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """计算多分片 FFT，产出频率轴、Max-Hold PSD、平均 PSD 与瀑布时间切片矩阵。
    
    返回:
    - freqs: 频率轴数组 (Hz), 长度 n_fft
    - max_psd: Max-Hold 保持功率谱 (dBFS)
    - avg_psd: 线性平均功率谱 (dBFS)
    - time_slices: (K, n_fft) 形状的时间-频率功率矩阵 (dBFS)
    - rbw_hz: 频率分辨率 (Hz)
    """
    if len(samples) < n_fft:
        # 零填充至至少一个完整 FFT 窗口
        pad_len = n_fft - len(samples)
        samples = np.pad(samples, (0, pad_len), mode="constant")

    hop = hop_size if hop_size is not None else (n_fft // 2)
    num_frames = (len(samples) - n_fft) // hop + 1
    if num_frames > max_slices:
        # 若样本过多，通过增大 hop_size 限制内存占用
        hop = max(hop, (len(samples) - n_fft) // max_slices + 1)
        num_frames = (len(samples) - n_fft) // hop + 1

    window, coherent_gain = get_window_and_coherent_gain(n_fft, window_name)
    rbw = sample_rate / n_fft
    freq_min = center_freq - sample_rate / 2.0
    freqs = freq_min + np.arange(n_fft, dtype=np.float64) * rbw

    slices = np.zeros((num_frames, n_fft), dtype=np.float32)
    eps = 1e-12

    for i in range(num_frames):
        start = i * hop
        segment = samples[start : start + n_fft] * window
        fft_raw = np.fft.fft(segment, n=n_fft)
        fft_shifted = np.fft.fftshift(fft_raw)
        magnitude = np.abs(fft_shifted) / coherent_gain
        slices[i] = 20.0 * np.log10(np.maximum(magnitude, eps))

    max_psd = np.max(slices, axis=0)
    # 线性域平均后转 dBFS
    linear_avg = np.mean(np.power(10.0, slices / 10.0), axis=0)
    avg_psd = 10.0 * np.log10(np.maximum(linear_avg, eps ** 2))

    return freqs, max_psd, avg_psd, slices, rbw


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


def detect_spectral_peaks(
    freqs: np.ndarray,
    max_psd: np.ndarray,
    avg_psd: np.ndarray,
    noise_floor_dbfs: float,
    threshold_db: float = 10.0,
    min_bandwidth_hz: float = 40_000.0,
    dc_center_freq: float | None = None,
    time_slices: np.ndarray | None = None,
    edge_margin_ratio: float = 0.04,
) -> list[dict]:
    """基于 CFAR 门限、谱峰聚类与多切片判决的载波感知算法。
    
    返回候选列表，包含频率、带宽、峰值功率、SNR、突发数等参数。
    """
    n_fft = len(freqs)
    rbw = freqs[1] - freqs[0] if n_fft > 1 else 1.0
    thresh_power = noise_floor_dbfs + threshold_db

    # 标记高于门限的活跃频点
    active = max_psd > thresh_power

    # 抑制边沿与零中频单个单点尖峰
    edge_bins = max(1, int(n_fft * edge_margin_ratio))
    active[:edge_bins] = False
    active[-edge_bins:] = False

    # 若指定 DC 中心，且 DC 处仅为 1~2 个孤立尖峰，则予以抑制
    if dc_center_freq is not None:
        dc_idx = int(np.argmin(np.abs(freqs - dc_center_freq)))
        if 0 <= dc_idx < n_fft:
            # 若 DC 邻域很窄（非宽带信号通过），清除它
            left_dc = max(0, dc_idx - 2)
            right_dc = min(n_fft, dc_idx + 3)
            if np.sum(active[left_dc:right_dc]) <= 2:
                active[left_dc:right_dc] = False

    # 寻找连续连通活跃区间
    diff = np.diff(active.astype(np.int32))
    starts = np.where(diff == 1)[0] + 1
    ends = np.where(diff == -1)[0] + 1

    if active[0]:
        starts = np.r_[0, starts]
    if active[-1]:
        ends = np.r_[ends, n_fft]

    if len(starts) == 0:
        return []

    # 缝合临近的小区间（针对 GFSK 双峰或偶发谷点，容忍 150kHz 内的凹坑）
    merge_gap_bins = max(2, int(150_000.0 / rbw))
    merged_regions: list[tuple[int, int]] = []
    cur_start, cur_end = starts[0], ends[0]

    for s, e in zip(starts[1:], ends[1:]):
        if s - cur_end <= merge_gap_bins:
            cur_end = e
        else:
            merged_regions.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    merged_regions.append((cur_start, cur_end))

    candidates: list[dict] = []
    total_slices = len(time_slices) if time_slices is not None else 1

    for reg_start, reg_end in merged_regions:
        reg_len = reg_end - reg_start
        # 寻找区间内的峰值点
        local_max_idx = reg_start + int(np.argmax(max_psd[reg_start:reg_end]))
        peak_pwr = float(max_psd[local_max_idx])
        peak_f = float(freqs[local_max_idx])

        # 抛物线二次插值精细修正峰值频率与功率
        if 0 < local_max_idx < n_fft - 1:
            alpha = float(max_psd[local_max_idx - 1])
            beta = float(max_psd[local_max_idx])
            gamma = float(max_psd[local_max_idx + 1])
            denom = alpha - 2.0 * beta + gamma
            if abs(denom) > 1e-5:
                delta = 0.5 * (alpha - gamma) / denom
                if abs(delta) <= 1.0:
                    peak_f = float(freqs[local_max_idx] + delta * rbw)
                    peak_pwr = float(beta - 0.25 * (alpha - gamma) * delta)

        snr = float(peak_pwr - noise_floor_dbfs)
        if snr < max(3.0, threshold_db * 0.7):
            continue

        # 计算 6-dB 下降带宽
        bw_drop_thresh = max(peak_pwr - 6.0, thresh_power - 3.0)
        bw_left = local_max_idx
        while bw_left > reg_start and max_psd[bw_left] > bw_drop_thresh:
            bw_left -= 1
        bw_right = local_max_idx
        while bw_right < reg_end - 1 and max_psd[bw_right] > bw_drop_thresh:
            bw_right += 1

        bw_hz = float(max((bw_right - bw_left) * rbw, rbw))
        center_f = float((freqs[bw_left] + freqs[bw_right]) / 2.0)

        # 忽略极窄且低信噪比的噪声随机扰动 (真实载波有一定带宽或高 SNR)
        if bw_hz < min_bandwidth_hz and snr < 20.0:
            continue

        # 统计瀑布图上的突发帧数 (burst_count)
        burst_count = 1
        if time_slices is not None:
            # 统计在多少个切片中，该区间的最大功率高于门限
            slice_peaks = np.max(time_slices[:, reg_start:reg_end], axis=1)
            burst_count = int(np.count_nonzero(slice_peaks > thresh_power))
            if burst_count == 0:
                burst_count = 1

        duty_cycle = round(float(burst_count) / float(total_slices), 3)

        candidates.append({
            "frequency_hz": center_f,
            "peak_freq_hz": peak_f,
            "bandwidth_hz": bw_hz,
            "peak_power_dbfs": round(peak_pwr, 1),
            "snr_db": round(snr, 1),
            "noise_floor_dbfs": round(noise_floor_dbfs, 1),
            "burst_count": burst_count,
            "duty_cycle": duty_cycle,
            "bin_range": (reg_start, reg_end),
        })

    return candidates
