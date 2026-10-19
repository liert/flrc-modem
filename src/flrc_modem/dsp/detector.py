"""多采样相位搜索前导，以 CRC 成功结果选择相位；噪声候选不输出。"""
from dataclasses import dataclass
import numpy as np

from ..config import DemodConfig
from ..models import DecodedFrame
from ..phy.packet import decode_packet
from ..phy.sync import PREFIX_BITS, TIMING_PREAMBLE, recover_sync_candidates
from .frontend import Frontend
from .shaping import preamble_contrast


@dataclass
class _Candidate:
    time_s: float
    cfo_hz: float
    power_db: float
    score: float
    preamble_errors: int
    coded_bits: np.ndarray
    sync_candidates: tuple[bytes, ...]


class BurstDemodulator:
    def __init__(self, config: DemodConfig):
        self.config = config
        self.frontend = Frontend(config)
        # 较小 BT 降低交替前导幅度。以 BT=1 原门限为基准按模型缩放。
        self.preamble_threshold = 0.45 * preamble_contrast(config.bt) / preamble_contrast(1.0)

    def demodulate(self, iq, frequency_hz: float, start_time_s: float = 0.0):
        """返回通过 CRC 的帧和候选突发数，业务解析不会参与判定。"""
        config, frontend = self.config, self.frontend
        processed = frontend.process(iq, frequency_hz - config.center_frequency)
        if processed is None:
            return [], 0
        filtered, power, background, cfo = processed
        candidates = []
        # 前导相关会在非 FLRC 射频突发内产生大量峰。用前缀和批量筛选，
        # 避免每个峰都进入 Python 循环计算功率和 21bit 定时前导。
        power_sum = np.concatenate(([0.0], np.cumsum(power, dtype=np.float64)))
        phase_step = max(1, int(round(frontend.sps / 12)))
        phases = np.arange(0, int(np.ceil(frontend.sps)), phase_step)
        count = int((len(filtered) - phases[-1] - 2) / frontend.sps)
        if count < 120:
            return [], 0
        # 所有采样相位在同一个 NumPy 批次完成，减少实时线程间的 GIL 交接。
        positions = phases[:, None] + np.arange(count) * frontend.sps
        indices = np.floor(positions).astype(np.int64)
        fraction = positions - indices
        symbols = filtered[indices] * (1 - fraction) + filtered[indices + 1] * fraction
        discriminator = np.angle(symbols[:, 1:] * np.conj(symbols[:, :-1]))
        # 交替符号的 32 点相关也可写成带符号前缀和，避免逐相位卷积。
        alternating = (-1.0) ** np.arange(discriminator.shape[1])
        sums = np.pad(np.cumsum(discriminator * alternating, axis=1), ((0, 0), (1, 0)))
        correlation = (sums[:, 32:] - sums[:, :-32]) * alternating[:count - 32] / 32
        phase_indices, peaks = np.nonzero(np.abs(correlation) > self.preamble_threshold)
        valid = peaks + PREFIX_BITS + 30 < discriminator.shape[1]
        phase_indices, peaks = phase_indices[valid], peaks[valid]
        if not len(peaks):
            return [], 0
        lo, hi = indices[phase_indices, peaks], indices[phase_indices, peaks + 32]
        local_powers = (power_sum[hi] - power_sum[lo]) / (hi - lo)
        strong = local_powers / background >= config.min_peak_ratio
        phase_indices, peaks, local_powers = phase_indices[strong], peaks[strong], local_powers[strong]
        if not len(peaks):
            return [], 0
        discriminator_sum = np.pad(np.cumsum(discriminator, axis=1), ((0, 0), (1, 0)))
        residuals = (discriminator_sum[phase_indices, peaks + 32] - discriminator_sum[phase_indices, peaks]) / 32
        reverse = discriminator[phase_indices, peaks] > residuals
        timing = discriminator[phase_indices[:, None], peaks[:, None] + np.arange(32, 53)] > residuals[:, None]
        timing ^= reverse[:, None]
        error_counts = np.count_nonzero(timing != TIMING_PREAMBLE, axis=1)
        for selected in np.flatnonzero(error_counts <= config.max_preamble_errors):
            phase, peak = int(phase_indices[selected]), int(peaks[selected])
            local_power = float(local_powers[selected])
            segment = discriminator[phase, peak:peak + PREFIX_BITS + config.max_coded_bits]
            residual = float(residuals[selected])
            bits = (segment > residual).astype(np.uint8)
            if bits[0]:
                bits ^= 1
            errors = int(error_counts[selected])
            syncs = ((config.sync_word,) if config.sync_word is not None
                     else recover_sync_candidates(bits[53:85]))
            time_s = start_time_s + (positions[phase, peak] - frontend.filter_delay) / config.work_rate
            candidates.append(_Candidate(
                time_s, cfo + residual * config.bit_rate / (2 * np.pi),
                float(10 * np.log10(max(local_power, np.finfo(float).tiny))),
                float(abs(correlation[phase, peak])), errors, bits[PREFIX_BITS:], syncs,
            ))
        # 相近时间的多采样相位只算一个突发，避免同一物理包重复输出。
        groups = []
        for candidate in sorted(candidates, key=lambda item: item.time_s):
            if not groups or candidate.time_s - groups[-1][0].time_s > 0.00025:
                groups.append([candidate])
            else:
                groups[-1].append(candidate)
        frames = []
        for group in groups:
            for candidate in sorted(group, key=lambda item: item.score, reverse=True):
                packet = decode_packet(candidate.coded_bits, candidate.sync_candidates, config.crc_initial,
                                       config.coding_rate)
                if packet is not None:
                    frames.append(DecodedFrame(
                        candidate.time_s, frequency_hz, candidate.cfo_hz,
                        candidate.power_db, candidate.score, candidate.preamble_errors, packet,
                    ))
                    break
        return frames, len(groups)
