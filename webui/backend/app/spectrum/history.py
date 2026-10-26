"""Bounded, sample-aligned raw IQ history; snapshots pin immutable blocks during export."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading


class IQHistoryError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class IQBlock:
    data: bytes
    timestamp: float
    sample_rate: float
    center_freq: float
    gap: bool
    sample_bytes: int

    @property
    def end_time(self):
        return self.timestamp + len(self.data) / (self.sample_bytes * self.sample_rate)


@dataclass(frozen=True)
class IQSnapshot:
    parts: tuple[memoryview, ...]
    time_start: float
    time_end: float
    sample_rate: float
    center_freq: float
    sample_bytes: int

    @property
    def samples_count(self):
        return sum(len(part) for part in self.parts) // self.sample_bytes


class IQHistory:
    def __init__(self, sample_bytes: int, *, max_seconds=20.0, max_bytes=256 * 1024**2, max_blocks=8192):
        if sample_bytes <= 0 or max_seconds <= 0 or max_bytes < sample_bytes or max_blocks < 1:
            raise ValueError('IQ history limits must be positive')
        self.sample_bytes = sample_bytes
        self.max_seconds, self.max_bytes, self.max_blocks = max_seconds, max_bytes, max_blocks
        self._blocks: deque[IQBlock] = deque()
        self._bytes = 0
        self._lock = threading.Lock()

    def append(self, raw, timestamp, sample_rate, center_freq, gap=False):
        if not raw:
            return
        if len(raw) % self.sample_bytes:
            raise ValueError('IQ block must contain complete samples')
        block = IQBlock(bytes(raw), timestamp, sample_rate, center_freq, gap, self.sample_bytes)
        with self._lock:
            if self._blocks and timestamp < self._blocks[-1].timestamp:
                self._blocks.clear()
                self._bytes = 0
            self._blocks.append(block)
            self._bytes += len(block.data)
            cutoff = block.end_time - self.max_seconds
            while self._blocks:
                first = self._blocks[0]
                trim = max(0, math.ceil((cutoff - first.timestamp) * first.sample_rate),
                           math.ceil((self._bytes - self.max_bytes) / self.sample_bytes)) * self.sample_bytes
                if len(self._blocks) > self.max_blocks or trim >= len(first.data):
                    self._bytes -= len(self._blocks.popleft().data)
                elif trim:
                    self._blocks[0] = IQBlock(first.data[trim:], first.timestamp + trim / (self.sample_bytes * first.sample_rate),
                                              first.sample_rate, first.center_freq, first.gap, self.sample_bytes)
                    self._bytes -= trim
                    break
                else:
                    break

    def status(self):
        with self._lock:
            return {
                'time_start': self._blocks[0].timestamp if self._blocks else None,
                'time_end': self._blocks[-1].end_time if self._blocks else None,
                'bytes': self._bytes, 'max_bytes': self.max_bytes, 'max_seconds': self.max_seconds,
            }

    def snapshot(self, start: float, end: float, *, sample_rate: float, center_freq: float) -> IQSnapshot:
        if not all(math.isfinite(v) for v in (start, end, sample_rate, center_freq)) or end <= start:
            raise IQHistoryError('invalid_selection', '选区时间范围无效，请重新框选')
        with self._lock:
            blocks = tuple(self._blocks)
        if not blocks:
            raise IQHistoryError('history_empty', '尚无可导出的原始 IQ，请先开始采集')
        tolerance = max(2 / sample_rate, math.ulp(max(abs(start), abs(end))) * 4)
        if start < blocks[0].timestamp - tolerance:
            raise IQHistoryError('history_expired', '选区原始 IQ 已过期或早于采集开始，请在 IQ 缓存时段内重新框选')
        if end > blocks[-1].end_time + tolerance:
            raise IQHistoryError('history_unavailable', '选区尚无完整 IQ 数据，请等待采集后重新框选')
        parts = []
        cursor = start
        actual_start = actual_end = None
        for block in blocks:
            if block.end_time <= start or block.timestamp >= end:
                continue
            if block.sample_rate != sample_rate or block.center_freq != center_freq:
                raise IQHistoryError('configuration_changed', '选区跨越采集参数变更，请在同一采样率和中心频率的时段内重新框选')
            if block.timestamp > cursor + tolerance or (parts and (block.gap or block.timestamp < cursor - tolerance)):
                raise IQHistoryError('history_gap', '选区包含接收中断或丢样，无法生成连续 IQ，请避开采集缺口重新框选')
            count = len(block.data) // self.sample_bytes
            first = max(0, round((start - block.timestamp) * sample_rate))
            last = min(count, round((end - block.timestamp) * sample_rate))
            if last <= first:
                continue
            parts.append(memoryview(block.data)[first * self.sample_bytes:last * self.sample_bytes])
            if actual_start is None:
                actual_start = block.timestamp + first / sample_rate
            actual_end = block.timestamp + last / sample_rate
            cursor = block.end_time
        if not parts or actual_end < end - tolerance:
            raise IQHistoryError('history_gap', '选区没有完整的连续 IQ 数据，请重新框选')
        return IQSnapshot(tuple(parts), actual_start, actual_end, sample_rate, center_freq, self.sample_bytes)
