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
        self.alternating_preamble = ((-1.0) ** np.arange(32)).astype(np.float32)
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
        phase_step = max(1, int(round(frontend.sps / 12)))
        for phase in range(0, int(np.ceil(frontend.sps)), phase_step):
            count = int((len(filtered) - phase - 2) / frontend.sps)
            if count < 120:
                continue
            positions = phase + np.arange(count) * frontend.sps
            indices = np.floor(positions).astype(np.int64)
            fraction = positions - indices
            symbols = filtered[indices] * (1 - fraction) + filtered[indices + 1] * fraction
            discriminator = np.angle(symbols[1:] * np.conj(symbols[:-1]))
            correlation = np.correlate(discriminator, self.alternating_preamble, mode="valid") / 32
            for peak in np.flatnonzero(np.abs(correlation) > self.preamble_threshold):
                if peak + PREFIX_BITS + 30 >= len(discriminator):
                    continue
                lo, hi = indices[peak], indices[min(peak + 32, len(indices) - 1)]
                local_power = float(np.mean(power[lo:hi]))
                if local_power / background < config.min_peak_ratio:
                    continue
                segment = discriminator[peak:peak + PREFIX_BITS + config.max_coded_bits]
                residual = float(np.mean(segment[:32]))
                bits = (segment > residual).astype(np.uint8)
                if bits[0]:
                    bits ^= 1
                errors = int(np.count_nonzero(bits[32:53] != TIMING_PREAMBLE))
                if errors > config.max_preamble_errors:
                    continue
                syncs = ((config.sync_word,) if config.sync_word is not None
                         else recover_sync_candidates(bits[53:85]))
                time_s = start_time_s + (positions[peak] - frontend.filter_delay) / config.work_rate
                candidates.append(_Candidate(
                    time_s, cfo + residual * config.bit_rate / (2 * np.pi),
                    float(10 * np.log10(max(local_power, np.finfo(float).tiny))),
                    float(abs(correlation[peak])), errors, bits[PREFIX_BITS:], syncs,
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
