"""统一的 FHSS + TDD 同步状态机。

管理同步生命周期（SEARCHING -> ACQUIRING -> LOCKED -> HOLDOVER -> LOST），
提供高置信度时序预测、误差边界及状态输出。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
from enum import Enum
from math import isfinite
from typing import Sequence

from .fhss import FHSSHopPrediction, FHSSSynchronizer
from .tdd import TDDMode, TDDPhase, TDDSynchronizer, TDDWindow
from ..models import DecodedFrame
from ..parsing.api import load_parser


class SyncState(Enum):
    SEARCHING = "searching"    # 初始搜网/盲听
    ACQUIRING = "acquiring"    # 捕获中（已获得 1~2 个有效物理锚点）
    LOCKED = "locked"          # 已锁定（时钟漂移收敛，时隙预测精确）
    HOLDOVER = "holdover"      # 保持态（发生信道衰落或短暂丢包，利用模型外推）
    LOST = "lost"              # 同步丢失（超过最大保持时间）


@dataclass(frozen=True)
class SyncSnapshot:
    """完整的同步状态预测快照。"""

    state: SyncState
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
    tdd_phase: TDDPhase
    tdd_window_remaining_s: float
    tdd_mode: TDDMode
    turnaround_guard_s: float
    clock_drift_ppm: float
    residual_s: float
    uncertainty_s: float
    confidence: float
    lock_duration_s: float
    anchor_count: int
    overflow_count: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["tdd_phase"] = self.tdd_phase.value
        d["tdd_mode"] = self.tdd_mode.value
        return d


class SyncStateMachine:
    """FHSS + TDD 统一同步引擎状态机。"""

    def __init__(
        self,
        hopping_table: Sequence[int] | Sequence[dict],
        *,
        sync_word: str | None = None,
        tdd_mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        dwell_seconds: float = 0.050,
        holdover_timeout_s: float = 5.0,
        max_drift_ppm: float = 200.0,
        protocol: str | None = "dk32s",
        bit_rate: float = 325000,
        coding_rate: str = "3/4",
    ):
        self.sync_word = bytes.fromhex(sync_word).hex().lower() if sync_word is not None else None
        self.holdover_timeout_s = float(holdover_timeout_s)
        self.parser = load_parser(protocol)
        self.last_ground_payload_len = 33

        self.fhss = FHSSSynchronizer(
            hopping_table,
            dwell_seconds=dwell_seconds,
            max_drift_ppm=max_drift_ppm,
        )
        self.tdd = TDDSynchronizer(mode=tdd_mode, slot_dwell_s=dwell_seconds,
                                   bitrate=int(bit_rate), coding_rate=coding_rate)

        self.state: SyncState = SyncState.SEARCHING
        self.first_lock_hw: float | None = None
        self.last_anchor_hw: float | None = None
        self.last_ground_tx_hw: float | None = None
        self.overflow_count: int = 0
        self.loss_reason: str | None = None
        self.frame_counts = Counter()
        self.rejection_counts = Counter()
        self.last_rejection: str | None = None
        self.last_parser_error: str | None = None
        self.accept_anchors_from_hw = 0.0

    def _reject(self, reason: str) -> bool:
        self.rejection_counts[reason] += 1
        self.last_rejection = reason
        return False

    def on_discontinuity(self, reason: str = "IQ 采样不连续或硬件溢出"):
        """当硬件发生丢样或流中断时调用。"""
        self.overflow_count += 1
        self.reset_clock(reason)

    def reset_clock(self, reason: str, *, not_before_hw: float | None = None):
        """丢弃旧时序并重新捕获；调谐恢复不计作新的硬件溢出。"""
        self.fhss.invalidate(reason)
        self.state = SyncState.SEARCHING
        self.first_lock_hw = None
        self.last_anchor_hw = None
        self.last_ground_tx_hw = None
        self.loss_reason = reason
        if not_before_hw is not None:
            self.accept_anchors_from_hw = max(self.accept_anchors_from_hw, not_before_hw)

    def on_frame(self, hw_time_s: float, frame: DecodedFrame) -> bool:
        """处理解调出的物理层帧及其应用层字段。"""
        if not isfinite(hw_time_s) or hw_time_s < 0:
            raise ValueError("hw_time_s 必须为非负有限数")
        if hw_time_s < self.accept_anchors_from_hw:
            return self._reject("stale_after_loss")

        # 校验物理 CRC
        if not frame.packet:
            return self._reject("missing_packet")

        # 检查同步字过滤
        frame_sync = frame.packet.sync_word.hex().lower()
        if self.sync_word is not None and frame_sync != self.sync_word:
            return self._reject("sync_mismatch")

        # 如果之前没有配置同步字，自学习当前第一个有效帧的同步字
        if self.sync_word is None:
            self.sync_word = frame_sync

        app = getattr(frame, "application", None)
        if app is None and self.parser is not None:
            try:
                app = self.parser(frame)
            except (ValueError, TypeError, IndexError) as error:
                self.last_parser_error = f"{type(error).__name__}: {error}"[:200]
                return self._reject("parser_error")
        app = app or {}
        direction = app.get("direction")
        self.frame_counts[direction if direction in ("ground_to_air", "air_to_ground") else "unknown"] += 1

        # 1. 提取地面遥控包的时间戳用于 FHSS 拟合
        if direction == "ground_to_air":
            ticks = app.get("timestamp_ticks")
            if ticks is None:
                return self._reject("missing_timestamp")
            if not app.get("sum8_ok", True):
                return self._reject("sum8_failed")
            if ticks is not None and app.get("sum8_ok", True):
                accepted = self.fhss.observe(hw_time_s, ticks)
                if accepted:
                    self.last_anchor_hw = hw_time_s
                    self.last_ground_tx_hw = hw_time_s
                    self.last_ground_payload_len = len(frame.packet.payload)
                    pred = self.fhss.predict(hw_time_s)
                    self.tdd.observe_ground(hw_time_s, pred.slot_start_hw_time,
                                            self.last_ground_payload_len)

                    # 状态跃迁
                    if self.fhss.locked:
                        if self.state != SyncState.LOCKED:
                            if self.first_lock_hw is None:
                                self.first_lock_hw = hw_time_s
                            self.state = SyncState.LOCKED
                            self.loss_reason = None
                    else:
                        self.state = SyncState.ACQUIRING
                    return True
                return self._reject("clock_rejected")

        # 2. 提取空中回传包用于更新 TDD 转换保护间隔
        elif direction == "air_to_ground":
            if self.last_ground_tx_hw is not None:
                self.tdd.observe_turnaround(self.last_ground_tx_hw, hw_time_s, self.last_ground_payload_len)
            return True

        return self._reject("unknown_direction")

    def step(self, current_hw_time_s: float):
        """定期调用状态机步进函数，检测超时并驱动状态机演进。"""
        if self.state in (SyncState.LOCKED, SyncState.HOLDOVER):
            if self.last_anchor_hw is not None:
                age = current_hw_time_s - self.last_anchor_hw
                if age > self.holdover_timeout_s:
                    reason = f"超时未收到新物理锚点 (age={age:.3f}s > {self.holdover_timeout_s:.3f}s)"
                    self.reset_clock(reason, not_before_hw=current_hw_time_s)
                    self.state = SyncState.LOST
                elif age > self.fhss.holdover_after_s:
                    self.state = SyncState.HOLDOVER

        elif self.state == SyncState.LOST:
            # 状态重置至 SEARCHING
            self.state = SyncState.SEARCHING

    def predict(self, target_hw_time_s: float) -> SyncSnapshot:
        """输出 target_hw_time_s 处的全量状态预测快照。"""
        self.step(target_hw_time_s)

        if not self.fhss.anchors:
            # 未捕获状态下的默认占位快照
            freq = self.fhss.frequencies[0]
            return SyncSnapshot(
                state=self.state,
                hw_time_s=target_hw_time_s,
                remote_time_s=0.0,
                timestamp_ticks=0,
                slot_index=0,
                frequency_hz=freq,
                slot_start_hw_time=target_hw_time_s,
                slot_end_hw_time=target_hw_time_s + self.fhss.dwell_seconds,
                slot_remaining_s=self.fhss.dwell_seconds,
                next_hop_hw_time=target_hw_time_s + self.fhss.dwell_seconds,
                next_slot_index=1 % self.fhss.slots_per_cycle,
                next_frequency_hz=self.fhss.frequencies[1 % self.fhss.slots_per_cycle],
                tdd_phase=TDDPhase.IDLE,
                tdd_window_remaining_s=0.0,
                tdd_mode=self.tdd.mode,
                turnaround_guard_s=self.tdd.estimated_turnaround_s,
                clock_drift_ppm=0.0,
                residual_s=0.0,
                uncertainty_s=999.0,
                confidence=0.0,
                lock_duration_s=0.0,
                anchor_count=0,
                overflow_count=self.overflow_count,
            )

        fhss_pred: FHSSHopPrediction = self.fhss.predict(target_hw_time_s)
        tdd_win: TDDWindow = self.tdd.classify_phase(
            target_hw_time_s,
            fhss_pred.slot_start_hw_time,
            self.last_ground_tx_hw,
        )

        lock_duration = (
            max(0.0, target_hw_time_s - self.first_lock_hw)
            if self.first_lock_hw is not None and self.state == SyncState.LOCKED
            else 0.0
        )

        window_remaining = max(0.0, tdd_win.end_hw_time - target_hw_time_s)

        return SyncSnapshot(
            state=self.state,
            hw_time_s=target_hw_time_s,
            remote_time_s=fhss_pred.remote_time_s,
            timestamp_ticks=fhss_pred.timestamp_ticks,
            slot_index=fhss_pred.slot_index,
            frequency_hz=fhss_pred.frequency_hz,
            slot_start_hw_time=fhss_pred.slot_start_hw_time,
            slot_end_hw_time=fhss_pred.slot_end_hw_time,
            slot_remaining_s=fhss_pred.slot_remaining_s,
            next_hop_hw_time=fhss_pred.next_hop_hw_time,
            next_slot_index=fhss_pred.next_slot_index,
            next_frequency_hz=fhss_pred.next_frequency_hz,
            tdd_phase=tdd_win.phase,
            tdd_window_remaining_s=window_remaining,
            tdd_mode=self.tdd.mode,
            turnaround_guard_s=self.tdd.estimated_turnaround_s,
            clock_drift_ppm=fhss_pred.clock_drift_ppm,
            residual_s=fhss_pred.residual_s,
            uncertainty_s=fhss_pred.uncertainty_s,
            confidence=fhss_pred.confidence,
            lock_duration_s=lock_duration,
            anchor_count=fhss_pred.anchor_count,
            overflow_count=self.overflow_count,
        )
