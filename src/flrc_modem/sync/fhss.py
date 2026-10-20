"""FHSS (跳频) 时钟与时隙同步引擎。

基于硬件时间戳 $t_{hw}$ 与远端时间戳 ticks 拟合时钟漂移与时间偏差：
remote_time = ref_remote + alpha * (t_hw - ref_hw)
实现跳频周期估计、时隙索引计算、信道映射及硬件跳频点预测。
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from math import floor, isfinite
from statistics import median
from typing import Sequence


TICK_RATE = 10000          # 1 tick = 100 µs
TICK_MODULUS = 1 << 32     # 32-bit tick wrap-around (~4.97 days)
DEFAULT_DWELL_S = 0.050    # 50 ms
DEFAULT_SLOTS_PER_CYCLE = 20


@dataclass(frozen=True)
class FHSSHopPrediction:
    """FHSS 跳频状态与时隙预测快照。"""

    hw_time_s: float
    remote_time_s: float
    timestamp_ticks: int
    slot_index: int
    frequency_hz: int
    slot_start_hw_time: float
    slot_end_hw_time: float
    slot_remaining_s: float
    next_hop_hw_time: float
    next_slot_index: int
    next_frequency_hz: int
    clock_drift_ppm: float
    residual_s: float
    uncertainty_s: float
    confidence: float
    state: str
    anchor_count: int

    def to_dict(self) -> dict:
        return asdict(self)


class FHSSSynchronizer:
    """FHSS 跳频同步器。

    负责在连续硬件采样时间轴上跟踪远程发送机时间戳，
    估计相对频差（ppm）与绝对相位，精确预测当前时隙及下一跳频时刻。
    """

    def __init__(
        self,
        hopping_table: Sequence[int] | Sequence[dict],
        *,
        dwell_seconds: float = DEFAULT_DWELL_S,
        tick_rate: int = TICK_RATE,
        window: int = 128,
        min_fit_span: float = 0.4,
        max_drift_ppm: float = 200.0,
        outlier_gate_s: float = 0.002,
        holdover_after_s: float = 0.15,
        max_uncertainty_s: float = 0.005,
    ):
        if not hopping_table:
            raise ValueError("hopping_table 不能为空")
        self.frequencies: list[int] = []
        for item in hopping_table:
            if isinstance(item, dict):
                self.frequencies.append(int(item["frequency_hz"]))
            else:
                self.frequencies.append(int(item))

        self.slots_per_cycle = len(self.frequencies)
        self.cycle_seconds = self.slots_per_cycle * dwell_seconds
        self.dwell_seconds = float(dwell_seconds)
        self.tick_rate = int(tick_rate)
        self.ticks_per_slot = round(self.dwell_seconds * self.tick_rate)
        self.ticks_per_cycle = self.slots_per_cycle * self.ticks_per_slot

        if not 3 <= window <= 256:
            raise ValueError("window 必须在 3..256 之间")
        for name, val in [
            ("min_fit_span", min_fit_span),
            ("max_drift_ppm", max_drift_ppm),
            ("outlier_gate_s", outlier_gate_s),
            ("holdover_after_s", holdover_after_s),
            ("max_uncertainty_s", max_uncertainty_s),
        ]:
            if not isfinite(val) or val <= 0:
                raise ValueError(f"{name} 必须为有限正数")

        self.window = window
        self.min_fit_span = float(min_fit_span)
        self.max_drift = float(max_drift_ppm) * 1e-6
        self.outlier_gate_s = float(outlier_gate_s)
        self.holdover_after_s = float(holdover_after_s)
        self.max_uncertainty_s = float(max_uncertainty_s)

        self.anchors: deque[tuple[float, float]] = deque(maxlen=window)
        self.rate: float = 1.0
        self.ref_hw: float = 0.0
        self.ref_remote: float = 0.0
        self.residual_s: float = 0.0
        self.accepted_count: int = 0
        self.rejected_count: int = 0
        self.generation: int = 0
        self.invalidation_reason: str | None = None

    def invalidate(self, reason: str = "硬件丢样或采样连续性中断"):
        """当硬件发生 OVERFLOW 或重新调谐时，清空历史锚点以避免污染时间基准。"""
        self.anchors.clear()
        self.generation += 1
        self.invalidation_reason = reason
        self.rate = 1.0
        self.ref_hw = 0.0
        self.ref_remote = 0.0
        self.residual_s = 0.0

    @property
    def locked(self) -> bool:
        """是否已建立稳定的双端时钟锁定。"""
        if len(self.anchors) < 3:
            return False
        span = self.anchors[-1][0] - self.anchors[0][0]
        return span >= self.min_fit_span and self.residual_s <= self.outlier_gate_s

    def _estimate_remote(self, hw_time_s: float) -> float:
        return self.ref_remote + self.rate * (hw_time_s - self.ref_hw)

    def _estimate_hw(self, remote_time_s: float) -> float:
        return self.ref_hw + (remote_time_s - self.ref_remote) / self.rate

    def _fit(self):
        pts = list(self.anchors)
        if len(pts) < 2:
            return

        ref_x = pts[-1][0]
        total_span = pts[-1][0] - pts[0][0]
        # 计算跨度足够的点对斜率中位数，避免 100µs 量化噪声干扰
        min_slope_span = max(self.min_fit_span / 2, total_span * 0.4)
        slopes = [
            (b[1] - a[1]) / (b[0] - a[0])
            for i, a in enumerate(pts)
            for b in pts[i + 1 :]
            if b[0] - a[0] >= min_slope_span and (b[0] - a[0]) > 0.005
        ]
        if slopes:
            med_rate = median(slopes)
            if abs(med_rate - 1.0) <= self.max_drift:
                self.rate = med_rate

        # 计算截距中位数
        intercepts = [y - self.rate * (x - ref_x) for x, y in pts]
        med_intercept = median(intercepts)

        # 门限过滤野值
        inliers = [
            (x, y)
            for x, y in pts
            if abs(y - (med_intercept + self.rate * (x - ref_x))) <= self.outlier_gate_s
        ]

        if not inliers:
            return

        self.rejected_count += len(pts) - len(inliers)
        self.anchors.clear()
        self.anchors.extend(inliers)

        self.ref_hw = inliers[-1][0]
        self.ref_remote = med_intercept + self.rate * (self.ref_hw - ref_x)
        self.residual_s = max(abs(y - self._estimate_remote(x)) for x, y in inliers)

    def observe(self, hw_time_s: float, timestamp_ticks: int) -> bool:
        """输入观测到的有效数据包物理到达时刻及其内含的 32bit 远端时间戳。"""
        if not isfinite(hw_time_s) or hw_time_s < 0:
            raise ValueError("hw_time_s 必须为非负有限数")
        if not isinstance(timestamp_ticks, int) or isinstance(timestamp_ticks, bool):
            raise TypeError("timestamp_ticks 必须为整数")
        if not (0 <= timestamp_ticks < TICK_MODULUS):
            raise ValueError(f"timestamp_ticks 越界：{timestamp_ticks}")

        # 时间单调性核验
        if self.anchors and hw_time_s <= self.anchors[-1][0]:
            self.rejected_count += 1
            return False

        remote = timestamp_ticks / self.tick_rate

        # 32bit 卷绕消解（Unwrap）
        if self.anchors:
            expected = self._estimate_remote(hw_time_s)
            wrap_period = TICK_MODULUS / self.tick_rate
            remote += round((expected - remote) / wrap_period) * wrap_period

            age = hw_time_s - self.anchors[-1][0]
            # 锁定状态下的野值门限判定
            allowed_error = self.outlier_gate_s + age * self.max_drift
            if self.locked and abs(remote - expected) > allowed_error:
                self.rejected_count += 1
                return False
        else:
            self.ref_hw = float(hw_time_s)
            self.ref_remote = remote

        self.anchors.append((float(hw_time_s), remote))
        self._fit()

        retained = any(x == hw_time_s for x, _ in self.anchors)
        if retained:
            self.accepted_count += 1
            self.invalidation_reason = None
        return retained

    def predict(self, hw_time_s: float) -> FHSSHopPrediction:
        """预测指定硬件时刻的 FHSS 跳频状态、时隙索引与下一跳频点。"""
        if not self.anchors:
            raise RuntimeError("尚无有效时间锚点，无法执行预测")

        remote_s = self._estimate_remote(hw_time_s)
        last_anchor_hw = self.anchors[-1][0]
        age_s = max(0.0, hw_time_s - last_anchor_hw)

        uncertainty_s = 0.5 / self.tick_rate + self.residual_s + age_s * self.max_drift
        drift_ppm = (self.rate - 1.0) * 1e6

        # 计算总 ticks
        total_ticks = floor(remote_s * self.tick_rate)
        wrapped_ticks = total_ticks % TICK_MODULUS
        cycle_ticks = wrapped_ticks % self.ticks_per_cycle

        slot_index = cycle_ticks // self.ticks_per_slot
        frequency_hz = self.frequencies[slot_index]

        # 计算当前时隙在远端时间轴上的起止时间
        slot_start_remote_s = (total_ticks - (cycle_ticks % self.ticks_per_slot)) / self.tick_rate
        slot_end_remote_s = slot_start_remote_s + self.dwell_seconds

        # 逆映射至 USRP 硬件时间轴
        slot_start_hw = self._estimate_hw(slot_start_remote_s)
        slot_end_hw = self._estimate_hw(slot_end_remote_s)
        slot_remaining_s = max(0.0, slot_end_hw - hw_time_s)

        next_hop_hw = slot_end_hw
        next_slot = (slot_index + 1) % self.slots_per_cycle
        next_freq_hz = self.frequencies[next_slot]

        # 确定状态
        if not self.locked:
            state = "acquiring"
        elif age_s > self.holdover_after_s:
            state = "holdover"
        elif uncertainty_s > self.max_uncertainty_s:
            state = "uncertain"
        else:
            state = "locked"

        # 计算置信度 (0.0 ~ 1.0)
        lock_factor = min(1.0, len(self.anchors) / 8.0) if self.locked else 0.3
        holdover_budget = max(self.holdover_after_s, (self.max_uncertainty_s - self.residual_s) / self.max_drift)
        age_decay = max(0.0, 1.0 - age_s / holdover_budget)
        res_factor = max(0.0, 1.0 - (self.residual_s / self.outlier_gate_s))
        confidence = float(max(0.0, min(1.0, lock_factor * age_decay * res_factor)))

        return FHSSHopPrediction(
            hw_time_s=hw_time_s,
            remote_time_s=remote_s,
            timestamp_ticks=wrapped_ticks,
            slot_index=slot_index,
            frequency_hz=frequency_hz,
            slot_start_hw_time=slot_start_hw,
            slot_end_hw_time=slot_end_hw,
            slot_remaining_s=slot_remaining_s,
            next_hop_hw_time=next_hop_hw,
            next_slot_index=next_slot,
            next_frequency_hz=next_freq_hz,
            clock_drift_ppm=drift_ppm,
            residual_s=self.residual_s,
            uncertainty_s=uncertainty_s,
            confidence=confidence,
            state=state,
            anchor_count=len(self.anchors),
        )
