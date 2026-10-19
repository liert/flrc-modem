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


class WindowDecoder:
    """离线和实时共用的窗口解调及去重状态，窗口应按时间顺序提交。"""
    def __init__(self, config: DemodConfig, stats: DecodeStats | None = None):
        self.config = config
        self.stats = stats if stats is not None else DecodeStats()
        self.demodulator = BurstDemodulator(config)
        self.recent = deque(maxlen=256)

    def decode(self, iq, start_time_s: float):
        self.stats.windows += 1
        window_frames = []
        for frequency in dict.fromkeys(self.config.frequencies):
            frames, candidates = self.demodulator.demodulate(iq, frequency, start_time_s)
            self.stats.candidate_groups += candidates
            window_frames.extend(frames)
        for frame in sorted(window_frames, key=lambda item: item.time_s):
            duplicate = any(
                old.frequency_hz == frame.frequency_hz
                and abs(old.time_s - frame.time_s) < 0.00025
                and old.packet.sync_word == frame.packet.sync_word
                and old.packet.payload == frame.packet.payload
                for old in self.recent
            )
            if duplicate:
                self.stats.duplicate_frames += 1
                continue
            self.recent.append(frame)
            self.stats.decoded_frames += 1
            yield frame


def iter_frames(path: Path, config: DemodConfig, stats: DecodeStats | None = None):
    decoder = WindowDecoder(config, stats)
    for start_time_s, iq in iter_iq_chunks(path, config):
        yield from decoder.decode(iq, start_time_s)
