"""USRP 硬件时间戳与采样索引恢复模块。

实现基于 UHD RXMetadata 和采样计数的高精度物理到达时间计算，
完全解耦于 Python 线程调度与操作系统缓冲延迟。
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from math import isfinite
import threading
from typing import NamedTuple

from ..sdr.usrp.abi import UHDRxMetadataErrorCode


@dataclass(frozen=True)
class HWTimestamp:
    """高精度硬件时间戳，保留 64bit 整数秒与小数秒，避免直接相加导致的尾数精度损失。"""

    full_secs: int
    frac_secs: float
    sample_index: int = 0
    sample_rate: float = 0.0

    def __post_init__(self):
        if not isinstance(self.full_secs, int) or isinstance(self.full_secs, bool):
            raise TypeError("full_secs 必须为整数")
        if not isfinite(self.frac_secs):
            raise ValueError("frac_secs 必须为有限浮点数")
        if self.sample_rate < 0:
            raise ValueError("sample_rate 不能为负数")

    @property
    def time_s(self) -> float:
        """返回精确的秒级浮点时间戳。"""
        offset = self.sample_index / self.sample_rate if (self.sample_rate > 0 and self.sample_index != 0) else 0.0
        return float(self.full_secs) + self.frac_secs + offset

    def advance_samples(self, samples: int) -> HWTimestamp:
        """沿采样时钟步进指定样本数。"""
        return HWTimestamp(
            full_secs=self.full_secs,
            frac_secs=self.frac_secs,
            sample_index=self.sample_index + samples,
            sample_rate=self.sample_rate,
        )

    def __sub__(self, other: HWTimestamp | float) -> float:
        if isinstance(other, HWTimestamp):
            return self.time_s - other.time_s
        elif isinstance(other, (int, float)):
            return self.time_s - float(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"HWTimestamp({self.full_secs}s + {self.frac_secs:.9f}s, samp={self.sample_index}, rate={self.sample_rate:.0f})"


class PacketArrival(NamedTuple):
    """数据包物理到达时刻描述。"""

    preamble_hw_time: HWTimestamp
    peak_sample_index: int
    filter_delay_samples: float
    sample_rate: float

    @property
    def time_s(self) -> float:
        return self.preamble_hw_time.time_s


class HWTimestampExtractor:
    """从 UHD RXMetadata 流或连续 IQ 采样流中提取连续硬件时间戳并处理丢样事件。"""

    def __init__(self, sample_rate: float):
        if not isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate 必须为有限正数")
        self.sample_rate = float(sample_rate)
        self.last_hw_anchor: HWTimestamp | None = None
        self.total_samples_received: int = 0
        self.overflow_count: int = 0
        self.discontinuity_flag: bool = False
        self.last_discontinuity_reason: str | None = None
        self.generation = 0
        self._next_hw_time: float | None = None
        self._spans = deque()
        self._lock = threading.RLock()

    def on_metadata(
        self,
        has_time: bool,
        full_secs: int,
        frac_secs: float,
        sample_count: int,
        error_code: int = UHDRxMetadataErrorCode.NONE,
    ) -> HWTimestamp | None:
        """处理 UHD recv 回调中的单次 metadata。

        当检测到 OVERFLOW 时标记时间连续性中断；
        当 has_time 为 True 时校准最新硬件基准。
        """
        with self._lock:
            if error_code == UHDRxMetadataErrorCode.OVERFLOW:
                self.overflow_count += 1
                self.generation += 1
                self.discontinuity_flag = True
                self.last_discontinuity_reason = "UHD RX overflow"
                self.last_hw_anchor = None
                self._next_hw_time = None
                self._spans.append((self.total_samples_received, None, self.generation))
                return None
            if error_code != UHDRxMetadataErrorCode.NONE or sample_count <= 0:
                return None
            if has_time:
                ts = HWTimestamp(full_secs, frac_secs, sample_rate=self.sample_rate)
                if self._next_hw_time is not None and abs(ts.time_s - self._next_hw_time) > 1.5 / self.sample_rate:
                    self.generation += 1
                self.discontinuity_flag = False
                self.last_discontinuity_reason = None
            elif self._next_hw_time is not None:
                full = int(self._next_hw_time)
                ts = HWTimestamp(full, self._next_hw_time - full, sample_rate=self.sample_rate)
            else:
                self.total_samples_received += sample_count
                return None
            self._spans.append((self.total_samples_received, ts.time_s, self.generation))
            self.total_samples_received += sample_count
            self._next_hw_time = ts.time_s + sample_count / self.sample_rate
            self.last_hw_anchor = ts
            return ts

    def window_time(self, start_sample: int, sample_count: int) -> tuple[float, int] | None:
        """Map a queued window using its own anchor; reject windows across gaps.

        Pruning follows the consumer, so RX advancing cannot move an older window.
        """
        with self._lock:
            while len(self._spans) > 1 and self._spans[1][0] <= start_sample:
                self._spans.popleft()
            if not self._spans or self._spans[0][0] > start_sample:
                return None
            start, hw_time, generation = self._spans[0]
            if hw_time is None:
                return None
            end = start_sample + sample_count
            if any(index < end and epoch != generation for index, _, epoch in self._spans):
                return None
            return hw_time + (start_sample - start) / self.sample_rate, generation

    def calculate_arrival(
        self,
        chunk_hw_time: HWTimestamp,
        peak_sample_index: int,
        filter_delay_samples: float = 0.0,
    ) -> PacketArrival:
        """根据前导峰值索引与滤波器时延，精确计算前导到达天线端的绝对硬件时间戳。

        公式：
        t_arrival = t_chunk + (peak_sample_index - filter_delay_samples) / sample_rate
        """
        exact_index = peak_sample_index - filter_delay_samples
        preamble_time = chunk_hw_time.advance_samples(round(exact_index))
        # 针对小数样本偏差，放入 frac_secs
        fractional_sample_offset = (exact_index - round(exact_index)) / self.sample_rate
        adjusted_time = HWTimestamp(
            full_secs=preamble_time.full_secs,
            frac_secs=preamble_time.frac_secs + fractional_sample_offset,
            sample_index=preamble_time.sample_index,
            sample_rate=self.sample_rate,
        )
        return PacketArrival(
            preamble_hw_time=adjusted_time,
            peak_sample_index=peak_sample_index,
            filter_delay_samples=filter_delay_samples,
            sample_rate=self.sample_rate,
        )

    def reset(self, reason: str = "手动重置基准"):
        """重置时间戳提取器。"""
        with self._lock:
            self.last_hw_anchor = None
            self._next_hw_time = None
            self.generation += 1
            self._spans.append((self.total_samples_received, None, self.generation))
            self.discontinuity_flag = True
            self.last_discontinuity_reason = reason
