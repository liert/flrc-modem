"""FLRC 前导模式、定时序列、同步字恢复与调制特征判决。"""
from __future__ import annotations

from typing import Any
import numpy as np

from ..config import DemodConfig
from ..dsp.frontend import Frontend
from ..dsp.shaping import preamble_contrast
from ..phy.packet import decode_packet
from ..phy.sync import PREFIX_BITS, TIMING_PREAMBLE, recover_sync_candidates


DEFAULT_BIT_RATES = (325_000.0, 1_300_000.0, 650_000.0, 260_000.0)


def verify_flrc_candidate(
    iq: np.ndarray,
    sample_rate: float,
    center_frequency: float,
    target_frequency: float,
    bandwidth_hz: float,
    candidate_bit_rates: tuple[float, ...] = DEFAULT_BIT_RATES,
    max_preamble_errors: int = 1,
) -> dict[str, Any]:
    """对能量候选载波执行 FLRC 前导相关与定时序列检验。
    
    返回字典包含：
    - is_flrc: bool
    - flrc_confidence: float (0.0 .. 1.0)
    - bit_rate: float | None
    - sync_words: list[str] (十六进制候选)
    - protocol_hint: str
    - burst_count: int
    - crc_valid_frames: int
    """
    if len(iq) < 256:
        return {
            "is_flrc": False,
            "flrc_confidence": 0.0,
            "bit_rate": None,
            "sync_words": [],
            "protocol_hint": _classify_non_flrc(bandwidth_hz),
            "burst_count": 0,
            "crc_valid_frames": 0,
        }

    # 根据估计带宽调整测试速率优先级
    sorted_rates = list(candidate_bit_rates)
    if bandwidth_hz > 900_000:
        sorted_rates.sort(key=lambda r: abs(r - 1_300_000.0))
    elif bandwidth_hz < 450_000:
        sorted_rates.sort(key=lambda r: abs(r - 325_000.0))

    offset_hz = target_frequency - center_frequency

    for br in sorted_rates:
        filter_cutoff = max(150_000.0, br * 350.0 / 325.0)
        # 若目标频偏加滤波已超过 Nyquist 带宽，则跳过
        if abs(offset_hz) + filter_cutoff >= sample_rate / 2.0:
            continue

        try:
            cfg = DemodConfig(
                sample_rate=sample_rate,
                center_frequency=center_frequency,
                frequencies=(target_frequency,),
                bit_rate=br,
                filter_cutoff=filter_cutoff,
                min_peak_ratio=2.5,
                max_preamble_errors=max_preamble_errors,
            )
        except ValueError:
            continue

        frontend = Frontend(cfg)
        processed = frontend.process(iq, offset_hz)
        if processed is None:
            continue

        filtered, power, background, cfo = processed
        phase_step = max(1, int(round(frontend.sps / 12)))
        phases = np.arange(0, int(np.ceil(frontend.sps)), phase_step)
        count = int((len(filtered) - phases[-1] - 2) / frontend.sps)
        if count < 120:
            continue

        positions = phases[:, None] + np.arange(count) * frontend.sps
        indices = np.floor(positions).astype(np.int64)
        fraction = positions - indices
        symbols = filtered[indices] * (1 - fraction) + filtered[indices + 1] * fraction
        discriminator = np.angle(symbols[:, 1:] * np.conj(symbols[:, :-1]))

        # 32 符号交替相关
        alternating = (-1.0) ** np.arange(discriminator.shape[1])
        sums = np.pad(np.cumsum(discriminator * alternating, axis=1), ((0, 0), (1, 0)))
        correlation = (sums[:, 32:] - sums[:, :-32]) * alternating[:count - 32] / 32

        # 预设判定门限 (按 BT 模型缩放)
        preamble_thresh = 0.45 * preamble_contrast(cfg.bt) / preamble_contrast(1.0)
        phase_indices, peaks = np.nonzero(np.abs(correlation) > preamble_thresh)
        valid = peaks + PREFIX_BITS + 30 < discriminator.shape[1]
        phase_indices, peaks = phase_indices[valid], peaks[valid]
        if not len(peaks):
            continue

        # 前缀和快速校验局部突发功率比
        power_sum = np.concatenate(([0.0], np.cumsum(power, dtype=np.float64)))
        lo, hi = indices[phase_indices, peaks], indices[phase_indices, peaks + 32]
        local_powers = (power_sum[hi] - power_sum[lo]) / np.maximum(hi - lo, 1)
        strong = (local_powers / background) >= cfg.min_peak_ratio
        phase_indices, peaks = phase_indices[strong], peaks[strong]
        if not len(peaks):
            continue

        discriminator_sum = np.pad(np.cumsum(discriminator, axis=1), ((0, 0), (1, 0)))
        residuals = (discriminator_sum[phase_indices, peaks + 32] - discriminator_sum[phase_indices, peaks]) / 32
        reverse = discriminator[phase_indices, peaks] > residuals
        timing = discriminator[phase_indices[:, None], peaks[:, None] + np.arange(32, 53)] > residuals[:, None]
        timing ^= reverse[:, None]
        error_counts = np.count_nonzero(timing != TIMING_PREAMBLE, axis=1)
        selected_indices = np.flatnonzero(error_counts <= cfg.max_preamble_errors)
        if not len(selected_indices):
            continue

        # 确认 FLRC 前导与定时序列！提取候选同步字
        sync_candidates_map: dict[str, int] = {}
        crc_frames = 0

        for sel in selected_indices:
            phase, peak = int(phase_indices[sel]), int(peaks[sel])
            residual = float(residuals[sel])
            # 读取包含前导与数据区的比特
            segment = discriminator[phase, peak : peak + PREFIX_BITS + 400]
            bits = (segment > residual).astype(np.uint8)
            if bits[0]:
                bits ^= 1

            if len(bits) >= 85:
                syncs = recover_sync_candidates(bits[53:85])
                for s in syncs:
                    h = s.hex()
                    sync_candidates_map[h] = sync_candidates_map.get(h, 0) + 1

                # 尝试 CRC 解调判定（若符合标准变长或固定长帧）
                for header_type in ("variable", "fixed"):
                    pkt = decode_packet(
                        bits[PREFIX_BITS:],
                        syncs,
                        crc_initial=cfg.crc_initial,
                        coding_rate=cfg.coding_rate,
                        header_type=header_type,
                        payload_lengths=cfg.payload_lengths,
                        crc_bytes=cfg.crc_bytes,
                        crc_initials=cfg.crc_initials,
                    )
                    if pkt is not None:
                        crc_frames += 1
                        break

        # 按出现频度排序候选同步字
        sorted_syncs = sorted(sync_candidates_map.keys(), key=lambda k: sync_candidates_map[k], reverse=True)

        confidence = 0.90 if crc_frames == 0 else 1.0
        min_err = int(np.min(error_counts[selected_indices]))
        if min_err == 0 and confidence < 1.0:
            confidence = 0.95

        br_khz = int(round(br / 1e3))
        protocol_hint = f"FLRC ({br_khz}k)"
        if crc_frames > 0:
            protocol_hint += " [CRC通过]"

        return {
            "is_flrc": True,
            "flrc_confidence": round(confidence, 2),
            "bit_rate": br,
            "sync_words": sorted_syncs[:4],
            "protocol_hint": protocol_hint,
            "burst_count": len(selected_indices),
            "crc_valid_frames": crc_frames,
        }

    # 未匹配到 FLRC
    return {
        "is_flrc": False,
        "flrc_confidence": 0.0,
        "bit_rate": None,
        "sync_words": [],
        "protocol_hint": _classify_non_flrc(bandwidth_hz),
        "burst_count": 0,
        "crc_valid_frames": 0,
    }


def _classify_non_flrc(bandwidth_hz: float) -> str:
    """根据信道占用带宽对非 FLRC 信号提供启发式分类。"""
    if bandwidth_hz >= 8_000_000.0:
        return "宽带信号 (Wi-Fi/OFDM 等)"
    elif 800_000.0 <= bandwidth_hz < 8_000_000.0:
        return "窄带载波 (BLE/GFSK 等)"
    elif bandwidth_hz <= 60_000.0:
        return "单音/窄带载波 (CW)"
    return "未知能量 (Non-FLRC)"
