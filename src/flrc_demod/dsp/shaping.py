"""收发共用的高斯成形模型及交替前导的相对判决幅度。"""
from functools import lru_cache
from math import ceil, log, pi, sqrt
import numpy as np
from scipy.signal import fftconvolve

from ..phy.profile import parse_bt


def shape_frequency(frequency, samples_per_bit: float, bt):
    bt = parse_bt(bt)
    if bt == 0:
        return np.asarray(frequency)
    # 以比特周期为时间单位，与绝对码率、采样率无关。
    sigma = sqrt(log(2)) / (2 * pi * bt)
    half = ceil(4 * samples_per_bit)
    time = np.arange(-half, half + 1) / samples_per_bit
    gaussian = np.exp(-0.5 * (time / sigma) ** 2)
    gaussian /= gaussian.sum()
    return fftconvolve(frequency, gaussian, mode="same")


@lru_cache(maxsize=3)
def preamble_contrast(bt) -> float:
    """一符号差分判决的最佳采样幅度，用于按 BT 缩放前导搜索门限。"""
    sps = 128
    alternating = np.repeat(np.tile([-1.0, 1.0], 16), sps)
    shaped = shape_frequency(alternating, sps, bt)
    # 丢弃首尾滤波过渡区。只缩放候选门限，不改变 CRC 判定。
    return float(np.mean(np.abs(shaped.reshape(32, sps).mean(axis=1)[8:-8])))
