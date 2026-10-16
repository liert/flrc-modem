"""编排输入、解调和输出；仅去重窗口重复，不合并业务层重传。"""
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import DemodConfig
from .dsp.detector import BurstDemodulator
from .io.iq import iter_iq_chunks


@dataclass
class DecodeStats:
    windows: int = 0
    candidate_groups: int = 0
    decoded_frames: int = 0
    duplicate_frames: int = 0


def iter_frames(path: Path, config: DemodConfig, stats: DecodeStats | None = None):
    stats = stats if stats is not None else DecodeStats()
    demodulator = BurstDemodulator(config)
    recent = deque(maxlen=256)
    for start_time_s, iq in iter_iq_chunks(path, config):
        stats.windows += 1
        window_frames = []
        for frequency in dict.fromkeys(config.frequencies):
            frames, candidates = demodulator.demodulate(iq, frequency, start_time_s)
            stats.candidate_groups += candidates
            window_frames.extend(frames)
        for frame in sorted(window_frames, key=lambda item: item.time_s):
            duplicate = any(
                old.frequency_hz == frame.frequency_hz
                and abs(old.time_s - frame.time_s) < 0.00025
                and old.packet.sync_word == frame.packet.sync_word
                and old.packet.payload == frame.packet.payload
                for old in recent
            )
            if duplicate:
                stats.duplicate_frames += 1
                continue
            recent.append(frame)
            stats.decoded_frames += 1
            yield frame
