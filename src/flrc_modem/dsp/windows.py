"""共享频谱窗函数与相干增益归一化。"""
import numpy as np


def get_window_and_coherent_gain(
    n_fft: int, window_name: str = "hann"
) -> tuple[np.ndarray, float]:
    """计算窗函数及相干增益归一化系数。"""
    w_name = window_name.lower().strip()
    if w_name in ("hann", "hanning"):
        window = np.hanning(n_fft).astype(np.float32)
    elif w_name == "hamming":
        window = np.hamming(n_fft).astype(np.float32)
    elif w_name == "blackman":
        window = np.blackman(n_fft).astype(np.float32)
    elif w_name in ("rect", "rectangular", "none"):
        window = np.ones(n_fft, dtype=np.float32)
    else:
        window = np.hanning(n_fft).astype(np.float32)

    coherent_gain = float(np.sum(window))
    if coherent_gain <= 0.0:
        coherent_gain = 1.0
    return window, coherent_gain
