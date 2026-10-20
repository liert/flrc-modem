"""TDD (时分双工) 交互同步与收发窗口估计模块。

支持两种候选交互机制：
1. Model A (FIXED_PERIODIC): 严格的固定周期 TDMA 时分双工；
2. Model B (RECEPTION_TRIGGERED): 半双工请求-响应式触发（实测空口验证机制）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Sequence


class TDDMode(Enum):
    FIXED_PERIODIC = "fixed_periodic"
    RECEPTION_TRIGGERED = "reception_triggered"


class TDDPhase(Enum):
    GROUND_TX = "ground_tx"
    TURNAROUND_GUARD = "turnaround_guard"
    AIR_RX = "air_rx"
    SLOT_GUARD = "slot_guard"
    IDLE = "idle"


@dataclass(frozen=True)
class TDDWindow:
    phase: TDDPhase
    start_hw_time: float
    end_hw_time: float
    duration_s: float
    subframe_index: int


def calculate_flrc_packet_duration(
    payload_len_bytes: int,
    bitrate: int = 325_000,
    coding_rate: str = "3/4",
) -> float:
    """计算 SX1280 FLRC 数据包在指定码率和编码率下的物理空中发射时长（秒）。

    结构：
    - 前导：32bit AGC + 21bit 定时前导 = 53 bits (无 FEC)
    - 差分同步字：32 bits
    - 硬件头：2 字节 (16 bits)
    - 载荷 + CRC24：(N + 3) 字节
    - 尾比特：CR=1/2 为 6 尾比特，CR=3/4 为 6 尾比特，CR=1 为 0
    """
    if coding_rate == "1/2":
        fec_rate = 2.0
        tail_bits = 6
    elif coding_rate == "3/4":
        fec_rate = 4.0 / 3.0
        tail_bits = 6
    elif coding_rate == "1":
        fec_rate = 1.0
        tail_bits = 0
    else:
        raise ValueError(f"不支持的 coding_rate: {coding_rate}")

    header_bits = 16 * fec_rate
    payload_and_crc_bits = (payload_len_bytes + 3) * 8 * fec_rate
    total_bits = 53 + 32 + header_bits + payload_and_crc_bits + tail_bits
    return float(total_bits / bitrate)


class TDDSynchronizer:
    """TDD 上下行时钟与收发窗口状态机。"""

    def __init__(
        self,
        mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        *,
        subframe_interval_s: float = 0.012,       # 12 ms
        nominal_turnaround_s: float = 0.000810,    # 810 µs
        nominal_ground_payload: int = 33,          # 33 Bytes
        nominal_air_payload: int = 7,              # 7 Bytes
        slot_dwell_s: float = 0.050,               # 50 ms
        slot_guard_s: float = 0.011,               # 11 ms
        bitrate: int = 325_000,
        coding_rate: str = "3/4",
    ):
        self.mode = mode
        self.subframe_interval_s = float(subframe_interval_s)
        self.nominal_turnaround_s = float(nominal_turnaround_s)
        self.slot_dwell_s = float(slot_dwell_s)
        self.slot_guard_s = float(slot_guard_s)
        self.bitrate = int(bitrate)
        self.coding_rate = coding_rate

        self.ground_duration_s = calculate_flrc_packet_duration(
            nominal_ground_payload, self.bitrate, self.coding_rate
        )
        self.air_duration_s = calculate_flrc_packet_duration(
            nominal_air_payload, self.bitrate, self.coding_rate
        )

        self.turnaround_samples: list[float] = []
        self.last_ground_arrival_hw: float | None = None
        self.last_air_arrival_hw: float | None = None

    @property
    def estimated_turnaround_s(self) -> float:
        """返回平滑估计的收发转换保护间隔（秒）。"""
        if self.turnaround_samples:
            return sum(self.turnaround_samples[-16:]) / len(self.turnaround_samples[-16:])
        return self.nominal_turnaround_s

    def observe_turnaround(self, ground_arrival_hw: float, air_arrival_hw: float, ground_len: int = 33):
        """输入一对关联的地面和空中数据包硬件到达时间，记录并更新射频转换时间估计。"""
        tx_duration = calculate_flrc_packet_duration(ground_len, self.bitrate, self.coding_rate)
        turnaround = air_arrival_hw - (ground_arrival_hw + tx_duration)
        if 0.0002 < turnaround < 0.0020:
            self.turnaround_samples.append(turnaround)
            self.last_ground_arrival_hw = ground_arrival_hw
            self.last_air_arrival_hw = air_arrival_hw

    def classify_phase(
        self,
        hw_time_s: float,
        slot_start_hw_time: float,
        last_ground_tx_hw_time: float | None = None,
    ) -> TDDWindow:
        """判断当前硬件时间戳在 TDD 交互周期中所处的具体收发阶段。

        阶段划分：
        1. GROUND_TX: 地面下行发射中；
        2. TURNAROUND_GUARD: 地面发射完毕，正在进行射频收发切换与空口等待；
        3. AIR_RX: 空中端回复正在到达；
        4. SLOT_GUARD: 时隙末尾的静默与调谐守候窗口；
        5. IDLE: 槽内各子帧之间的静默空闲。
        """
        elapsed_in_slot = hw_time_s - slot_start_hw_time

        # 检查是否进入时隙末尾的调谐守候窗口
        if elapsed_in_slot >= (self.slot_dwell_s - self.slot_guard_s):
            guard_start = slot_start_hw_time + (self.slot_dwell_s - self.slot_guard_s)
            guard_end = slot_start_hw_time + self.slot_dwell_s
            return TDDWindow(TDDPhase.SLOT_GUARD, guard_start, guard_end, self.slot_guard_s, -1)

        # 在 Model B (接收触发式) 模式下，若传入了最近一次地面发送时刻
        if self.mode == TDDMode.RECEPTION_TRIGGERED and last_ground_tx_hw_time is not None:
            since_tx = hw_time_s - last_ground_tx_hw_time
            if 0 <= since_tx < self.ground_duration_s:
                return TDDWindow(
                    TDDPhase.GROUND_TX,
                    last_ground_tx_hw_time,
                    last_ground_tx_hw_time + self.ground_duration_s,
                    self.ground_duration_s,
                    0,
                )
            turnaround_end = last_ground_tx_hw_time + self.ground_duration_s + self.estimated_turnaround_s
            if self.ground_duration_s <= since_tx < (self.ground_duration_s + self.estimated_turnaround_s):
                return TDDWindow(
                    TDDPhase.TURNAROUND_GUARD,
                    last_ground_tx_hw_time + self.ground_duration_s,
                    turnaround_end,
                    self.estimated_turnaround_s,
                    0,
                )
            rx_end = turnaround_end + self.air_duration_s
            if (self.ground_duration_s + self.estimated_turnaround_s) <= since_tx < (self.ground_duration_s + self.estimated_turnaround_s + self.air_duration_s):
                return TDDWindow(
                    TDDPhase.AIR_RX,
                    turnaround_end,
                    rx_end,
                    self.air_duration_s,
                    0,
                )

        # 在 Model A 或普通周期性投影下：按 12ms 子帧周期划分
        # 实测首个包起始于 ~1.7ms
        subframe_offset = 0.0017
        rel_time = elapsed_in_slot - subframe_offset
        if rel_time < 0:
            return TDDWindow(TDDPhase.IDLE, slot_start_hw_time, slot_start_hw_time + subframe_offset, subframe_offset, 0)

        subframe_idx = int(rel_time // self.subframe_interval_s)
        time_in_subframe = rel_time % self.subframe_interval_s
        subframe_start = slot_start_hw_time + subframe_offset + subframe_idx * self.subframe_interval_s

        tx_end = subframe_start + self.ground_duration_s
        if time_in_subframe < self.ground_duration_s:
            return TDDWindow(TDDPhase.GROUND_TX, subframe_start, tx_end, self.ground_duration_s, subframe_idx)

        turnaround_end = tx_end + self.estimated_turnaround_s
        if time_in_subframe < (self.ground_duration_s + self.estimated_turnaround_s):
            return TDDWindow(TDDPhase.TURNAROUND_GUARD, tx_end, turnaround_end, self.estimated_turnaround_s, subframe_idx)

        rx_end = turnaround_end + self.air_duration_s
        if time_in_subframe < (self.ground_duration_s + self.estimated_turnaround_s + self.air_duration_s):
            return TDDWindow(TDDPhase.AIR_RX, turnaround_end, rx_end, self.air_duration_s, subframe_idx)

        next_subframe_start = subframe_start + self.subframe_interval_s
        return TDDWindow(TDDPhase.IDLE, rx_end, next_subframe_start, next_subframe_start - rx_end, subframe_idx)
