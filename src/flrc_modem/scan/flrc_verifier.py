"""FLRC 前导模式、定时序列、同步字恢复与调制特征判决。"""
from __future__ import annotations

from typing import Any
from threading import Event
import numpy as np

from ..config import DemodConfig
from ..dsp.detector import BurstDemodulator
from .cancellation import check_cancelled


DEFAULT_BIT_RATES = (325_000.0, 1_300_000.0, 650_000.0, 260_000.0)


def verify_flrc_candidate(
    iq: np.ndarray,
    sample_rate: float,
    center_frequency: float,
    target_frequency: float,
    bandwidth_hz: float,
    candidate_bit_rates: tuple[float, ...] = DEFAULT_BIT_RATES,
    max_preamble_errors: int = 1,
    cancel_event: Event | None = None,
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
    check_cancelled(cancel_event)
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
        check_cancelled(cancel_event)
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
                header_type="auto",
            )
        except ValueError:
            continue

        analysis = BurstDemodulator(cfg).analyze(
            iq, target_frequency, check_cancelled=lambda: check_cancelled(cancel_event),
        )
        if not analysis.burst_count:
            continue
        crc_frames = len(analysis.frames)
        confidence = 1.0 if crc_frames else (0.95 if analysis.min_preamble_errors == 0 else 0.90)

        br_khz = int(round(br / 1e3))
        protocol_hint = f"FLRC ({br_khz}k)"
        if crc_frames > 0:
            protocol_hint += " [CRC通过]"

        return {
            "is_flrc": True,
            "flrc_confidence": round(confidence, 2),
            "bit_rate": br,
            "sync_words": [word.hex() for word in analysis.sync_words[:4]],
            "protocol_hint": protocol_hint,
            "burst_count": analysis.burst_count,
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
