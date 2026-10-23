"""多采样相位搜索前导，以 CRC 成功结果选择相位；噪声候选不输出。"""
from dataclasses import dataclass
from collections import Counter
from typing import Callable
import numpy as np

from ..config import DemodConfig
from ..models import DecodedFrame
from ..phy.packet import decode_packet
from ..phy.sync import PREFIX_BITS, TIMING_PREAMBLE, expected_sync_raw_bits, recover_sync_candidates, sync_raw_errors
from .frontend import Frontend
from .shaping import preamble_contrast


@dataclass
class _Candidate:
    time_s: float
    cfo_hz: float
    power_db: float
    score: float
    preamble_errors: int
    coded_bits: np.ndarray | None
    sync_candidates: tuple[bytes, ...]
    phase_offset: float = 0.0
    peak: int = 0
    residual: float = 0.0
    rev: int = 0


@dataclass
class BurstAnalysis:
    """共享检测结果；候选统计按物理突发去重，与 CRC 帧分开报告。"""
    frames: list[DecodedFrame]
    burst_count: int = 0
    sync_words: tuple[bytes, ...] = ()
    min_preamble_errors: int | None = None
    burst_times: tuple[float, ...] = ()


def _sym_at_kernel(filtered, ph: float, sps: float, k: int):
    pos = ph + k * sps
    idx = int(np.floor(pos))
    frac = pos - idx
    return filtered[idx] * (1.0 - frac) + filtered[idx + 1] * frac


def _disc_at_kernel(filtered, ph: float, sps: float, k: int, disc, has_disc):
    if has_disc[k]:
        return disc[k]
    s0 = _sym_at_kernel(filtered, ph, sps, k)
    s1 = _sym_at_kernel(filtered, ph, sps, k + 1)
    prod = s1 * np.conj(s0)
    d = np.arctan2(prod.imag, prod.real)
    disc[k] = d
    has_disc[k] = 1
    return d


def _scan_preambles_kernel(filtered, power_sum, background: float, min_peak_ratio: float,
                           phases, sps: float, count: int, preamble_threshold: float,
                           timing_preamble, max_preamble_errors: int, prefix_bits: int):
    """仅在 32 符号局部功率达标的区间递推计算鉴频角与前导相关，与全矩阵计算数学等价。"""
    n_phases = len(phases)
    m = count - 1
    max_start = m - (prefix_bits + 30)
    if max_start <= 0:
        return (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.float64), np.empty(0, dtype=np.uint8),
                np.empty(0, dtype=np.int64), np.empty((0, 32), dtype=np.uint8))

    min_local = background * min_peak_ratio
    pre_thresh = preamble_threshold * 32.0 - 1e-9
    disc = np.empty(m, dtype=np.float64)
    has_disc = np.empty(m, dtype=np.uint8)

    cap = 128
    out_phase = np.empty(cap, dtype=np.int64)
    out_peak = np.empty(cap, dtype=np.int64)
    out_power = np.empty(cap, dtype=np.float64)
    out_score = np.empty(cap, dtype=np.float64)
    out_res = np.empty(cap, dtype=np.float64)
    out_rev = np.empty(cap, dtype=np.uint8)
    out_err = np.empty(cap, dtype=np.int64)
    out_sync = np.empty((cap, 32), dtype=np.uint8)
    n_out = 0

    for p in range(n_phases):
        ph = phases[p]
        has_disc[:] = 0
        prev_strong = -2
        alt_sum = 0.0
        prev_sym = 0j
        for peak in range(max_start):
            lo = int(np.floor(ph + peak * sps))
            hi = int(np.floor(ph + (peak + 32) * sps))
            local_power = (power_sum[hi] - power_sum[lo]) / (hi - lo)
            if local_power < min_local:
                continue
            if peak == prev_strong + 1:
                cur_sym = _sym_at_kernel(filtered, ph, sps, peak + 32)
                if has_disc[peak + 31]:
                    d_new = disc[peak + 31]
                else:
                    prod = cur_sym * np.conj(prev_sym)
                    d_new = np.arctan2(prod.imag, prod.real)
                    disc[peak + 31] = d_new
                    has_disc[peak + 31] = 1
                prev_sym = cur_sym
                alt_sum = disc[peak - 1] - alt_sum - d_new
            else:
                alt_sum = 0.0
                sign = 1.0
                s_prev = _sym_at_kernel(filtered, ph, sps, peak)
                for k in range(peak, peak + 32):
                    s_cur = _sym_at_kernel(filtered, ph, sps, k + 1)
                    if has_disc[k]:
                        d = disc[k]
                    else:
                        prod = s_cur * np.conj(s_prev)
                        d = np.arctan2(prod.imag, prod.real)
                        disc[k] = d
                        has_disc[k] = 1
                    s_prev = s_cur
                    alt_sum += d * sign
                    sign = -sign
                prev_sym = s_prev
            prev_strong = peak

            if abs(alt_sum) <= pre_thresh:
                continue
            exact_alt = 0.0
            plain_sum = 0.0
            sign = 1.0
            for k in range(peak, peak + 32):
                d = disc[k]
                exact_alt += d * sign
                plain_sum += d
                sign = -sign
            score = abs(exact_alt * (1.0 / 32.0))
            if score <= preamble_threshold:
                continue
            residual = plain_sum * (1.0 / 32.0)
            rev = 1 if disc[peak] > residual else 0
            errs = 0
            for t_idx in range(21):
                d = _disc_at_kernel(filtered, ph, sps, peak + 32 + t_idx, disc, has_disc)
                bit = (1 if d > residual else 0) ^ rev
                if bit != timing_preamble[t_idx]:
                    errs += 1
                    if errs > max_preamble_errors:
                        break
            if errs <= max_preamble_errors:
                if n_out == cap:
                    out_phase = np.concatenate((out_phase, np.empty(cap, dtype=np.int64)))
                    out_peak = np.concatenate((out_peak, np.empty(cap, dtype=np.int64)))
                    out_power = np.concatenate((out_power, np.empty(cap, dtype=np.float64)))
                    out_score = np.concatenate((out_score, np.empty(cap, dtype=np.float64)))
                    out_res = np.concatenate((out_res, np.empty(cap, dtype=np.float64)))
                    out_rev = np.concatenate((out_rev, np.empty(cap, dtype=np.uint8)))
                    out_err = np.concatenate((out_err, np.empty(cap, dtype=np.int64)))
                    out_sync = np.concatenate((out_sync, np.empty((cap, 32), dtype=np.uint8)))
                    cap *= 2
                for s_idx in range(32):
                    d = _disc_at_kernel(filtered, ph, sps, peak + 53 + s_idx, disc, has_disc)
                    out_sync[n_out, s_idx] = (1 if d > residual else 0) ^ rev
                out_phase[n_out] = p
                out_peak[n_out] = peak
                out_power[n_out] = local_power
                out_score[n_out] = score
                out_res[n_out] = residual
                out_rev[n_out] = rev
                out_err[n_out] = errs
                n_out += 1

    return (out_phase[:n_out], out_peak[:n_out], out_power[:n_out],
            out_score[:n_out], out_res[:n_out], out_rev[:n_out],
            out_err[:n_out], out_sync[:n_out])


def _extract_coded_bits_kernel(filtered, ph: float, sps: float, start_k: int, end_k: int,
                               residual: float, rev: int):
    """按需提取单个候选的编码比特，避免为未选中相位或同步字不匹配的候选做全身插值。"""
    n = end_k - start_k
    bits = np.empty(n, dtype=np.uint8)
    s_prev = _sym_at_kernel(filtered, ph, sps, start_k)
    for i in range(n):
        s_cur = _sym_at_kernel(filtered, ph, sps, start_k + i + 1)
        prod = s_cur * np.conj(s_prev)
        d = np.arctan2(prod.imag, prod.real)
        bits[i] = (1 if d > residual else 0) ^ rev
        s_prev = s_cur
    return bits


try:
    from numba import njit
except ImportError:
    _sym_at_kernel = None
    _disc_at_kernel = None
    _scan_preambles_jit = None
    _extract_coded_bits_jit = None
else:
    _sym_at_kernel = njit(cache=True, nogil=True)(_sym_at_kernel)
    _disc_at_kernel = njit(cache=True, nogil=True)(_disc_at_kernel)
    _scan_preambles_jit = njit(cache=True, nogil=True)(_scan_preambles_kernel)
    _extract_coded_bits_jit = njit(cache=True, nogil=True)(_extract_coded_bits_kernel)


def warm_up_dsp() -> None:
    """预热前端与检测器的 JIT 内核，避免实时线程首窗承担编译或缓存加载开销。"""
    from .frontend import _jit_cfo_angles, _jit_derotate, _jit_power_stats
    if _jit_power_stats is not None:
        buf64 = np.ones(16, dtype=np.complex64)
        power, power_sum, peak, _ = _jit_power_stats(buf64, 4)
        _jit_cfo_angles(buf64, power, peak, 2, 4, 2_500_000.0)
        _jit_derotate(buf64, 100.0, 2_500_000.0)
    if _scan_preambles_jit is not None:
        buf128 = np.ones(600, dtype=np.complex128)
        ps = np.arange(601, dtype=np.float64)
        _scan_preambles_jit(buf128, ps, 0.1, 3.0, np.array([0.0]), 4.0, 130,
                            0.45, TIMING_PREAMBLE, 1, PREFIX_BITS)
        _extract_coded_bits_jit(buf128, 0.0, 4.0, 0, 8, 0.0, 0)


class BurstDemodulator:
    def __init__(self, config: DemodConfig):
        self.config = config
        self.frontend = Frontend(config)
        # 较小 BT 降低交替前导幅度。以 BT=1 原门限为基准按模型缩放。
        self.preamble_threshold = 0.45 * preamble_contrast(config.bt) / preamble_contrast(1.0)
        # 已知同步字时，在判决域先比对同步区，异源突发不再进入逐候选 Viterbi。
        self._sync_expected = (expected_sync_raw_bits(config.sync_candidates)
                               if config.sync_candidates and config.max_sync_errors < 31 else None)

    def demodulate(self, iq, frequency_hz: float, start_time_s: float = 0.0):
        """返回通过 CRC 的帧和候选突发数，业务解析不会参与判定。"""
        result = self.analyze(iq, frequency_hz, start_time_s)
        return result.frames, result.burst_count

    def analyze(self, iq, frequency_hz: float, start_time_s: float = 0.0,
                check_cancelled: Callable[[], None] | None = None) -> BurstAnalysis:
        """扫描和正式解调共用前导搜索、分组、完整帧解码与去重。"""
        def checkpoint():
            if check_cancelled is not None:
                check_cancelled()

        checkpoint()
        config, frontend = self.config, self.frontend
        processed = frontend.process(iq, frequency_hz - config.center_frequency)
        checkpoint()
        if processed is None:
            return BurstAnalysis([])
        filtered, power, background, cfo = processed
        # 复用 Frontend 已计算的前缀和；若测试替身直接返回自定义 power 则按需补算。
        if getattr(frontend, "_last_power", None) is power and frontend._last_power_sum is not None:
            power_sum = frontend._last_power_sum
        else:
            power_sum = np.concatenate(([0.0], np.cumsum(power, dtype=np.float64)))
        phase_step = max(1, int(round(frontend.sps / 12)))
        phases = np.arange(0, int(np.ceil(frontend.sps)), phase_step, dtype=np.float64)
        count = int((len(filtered) - phases[-1] - 2) / frontend.sps)
        if count < 120:
            return BurstAnalysis([])

        candidates = []
        if _scan_preambles_jit is not None:
            filtered_c128 = np.ascontiguousarray(filtered, dtype=np.complex128)
            phase_indices, peaks, local_powers, scores, residuals, reverses, error_counts, sync_raw = (
                _scan_preambles_jit(
                    filtered_c128, power_sum, float(background), float(config.min_peak_ratio),
                    phases, float(frontend.sps), count, float(self.preamble_threshold),
                    TIMING_PREAMBLE, int(config.max_preamble_errors), PREFIX_BITS,
                )
            )
            checkpoint()
            if not len(peaks):
                return BurstAnalysis([])
            selected_indices = np.arange(len(peaks))
            if self._sync_expected is not None:
                selected_indices = selected_indices[
                    sync_raw_errors(sync_raw, self._sync_expected) <= config.max_sync_errors
                ]
            for selected in selected_indices:
                phase = int(phase_indices[selected])
                peak = int(peaks[selected])
                local_power = float(local_powers[selected])
                residual = float(residuals[selected])
                rev = int(reverses[selected])
                errors = int(error_counts[selected])
                syncs = config.sync_candidates or recover_sync_candidates(sync_raw[selected])
                pos = float(phases[phase]) + peak * frontend.sps
                time_s = start_time_s + (pos - frontend.filter_delay) / config.work_rate
                candidates.append(_Candidate(
                    time_s, cfo + residual * config.bit_rate / (2 * np.pi),
                    float(10 * np.log10(max(local_power, np.finfo(float).tiny))),
                    float(scores[selected]), errors, None, syncs,
                    float(phases[phase]), peak, residual, rev,
                ))
        else:
            filtered_c128 = filtered
            positions = phases[:, None] + np.arange(count) * frontend.sps
            indices = np.floor(positions).astype(np.int64)
            fraction = positions - indices
            symbols = filtered[indices] * (1 - fraction) + filtered[indices + 1] * fraction
            discriminator = np.angle(symbols[:, 1:] * np.conj(symbols[:, :-1]))
            alternating = (-1.0) ** np.arange(discriminator.shape[1])
            sums = np.pad(np.cumsum(discriminator * alternating, axis=1), ((0, 0), (1, 0)))
            correlation = (sums[:, 32:] - sums[:, :-32]) * alternating[:count - 32] / 32
            phase_indices, peaks = np.nonzero(np.abs(correlation) > self.preamble_threshold)
            valid = peaks + PREFIX_BITS + 30 < discriminator.shape[1]
            phase_indices, peaks = phase_indices[valid], peaks[valid]
            checkpoint()
            if not len(peaks):
                return BurstAnalysis([])
            lo, hi = indices[phase_indices, peaks], indices[phase_indices, peaks + 32]
            local_powers = (power_sum[hi] - power_sum[lo]) / (hi - lo)
            strong = local_powers / background >= config.min_peak_ratio
            phase_indices, peaks, local_powers = phase_indices[strong], peaks[strong], local_powers[strong]
            if not len(peaks):
                return BurstAnalysis([])
            discriminator_sum = np.pad(np.cumsum(discriminator, axis=1), ((0, 0), (1, 0)))
            residuals = (discriminator_sum[phase_indices, peaks + 32] - discriminator_sum[phase_indices, peaks]) / 32
            reverse = discriminator[phase_indices, peaks] > residuals
            timing = discriminator[phase_indices[:, None], peaks[:, None] + np.arange(32, 53)] > residuals[:, None]
            timing ^= reverse[:, None]
            error_counts = np.count_nonzero(timing != TIMING_PREAMBLE, axis=1)
            accepted = np.flatnonzero(error_counts <= config.max_preamble_errors)
            if self._sync_expected is not None and len(accepted):
                sync_raw = discriminator[phase_indices[accepted, None], peaks[accepted, None] + np.arange(53, PREFIX_BITS)]
                sync_raw = (sync_raw > residuals[accepted, None]) ^ reverse[accepted, None]
                accepted = accepted[sync_raw_errors(sync_raw, self._sync_expected) <= config.max_sync_errors]
            for selected in accepted:
                phase, peak = int(phase_indices[selected]), int(peaks[selected])
                local_power = float(local_powers[selected])
                segment = discriminator[phase, peak:peak + PREFIX_BITS + config.max_coded_bits]
                residual = float(residuals[selected])
                bits = (segment > residual).astype(np.uint8)
                if bits[0]:
                    bits ^= 1
                errors = int(error_counts[selected])
                syncs = config.sync_candidates or recover_sync_candidates(bits[53:85])
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
        sync_counts = Counter()
        limit = config.max_burst_attempts
        max_sym = count - 1
        for group in groups:
            checkpoint()
            # 一个物理突发对同一同步字只投一次票，避免采样相位偏置。
            sync_counts.update(tuple(dict.fromkeys(word for candidate in group for word in candidate.sync_candidates)))
            # 相邻采样相位常给出完全相同的硬判决，只解一次；按分数限额避免坏包拖垮实时线程。
            tried = set()
            for candidate in sorted(group, key=lambda item: item.score, reverse=True):
                checkpoint()
                if limit and len(tried) >= limit:
                    break
                coded_bits = candidate.coded_bits
                if coded_bits is None:
                    end_k = min(max_sym, candidate.peak + PREFIX_BITS + config.max_coded_bits)
                    coded_bits = _extract_coded_bits_jit(
                        filtered_c128, candidate.phase_offset, float(frontend.sps),
                        candidate.peak + PREFIX_BITS, end_k, candidate.residual, candidate.rev,
                    )
                    candidate.coded_bits = coded_bits
                key = (coded_bits.tobytes(), candidate.sync_candidates)
                if key in tried:
                    continue
                tried.add(key)
                packet = decode_packet(coded_bits, candidate.sync_candidates, config.crc_initial,
                                       config.coding_rate, header_type=config.header_type,
                                       payload_lengths=config.payload_lengths, crc_bytes=config.crc_bytes,
                                       crc_initials=config.crc_initials)
                if packet is not None:
                    frames.append(DecodedFrame(
                        candidate.time_s, frequency_hz, candidate.cfo_hz,
                        candidate.power_db, candidate.score, candidate.preamble_errors, packet,
                    ))
                    break
        verified_syncs = tuple(dict.fromkeys(frame.packet.sync_word for frame in frames))
        sync_words = tuple(dict.fromkeys((*verified_syncs, *(word for word, _ in sync_counts.most_common()))))
        return BurstAnalysis(frames, len(groups), sync_words,
                             min((candidate.preamble_errors for candidate in candidates), default=None),
                             tuple(group[0].time_s for group in groups))
