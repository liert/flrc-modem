"""USRP N310 UHD Timed Commands 硬件跳频调度与 AD9361 调谐时间评估。

通过 UHD timed commands 在 USRP 硬件时间戳对齐的时刻触发 AD9361 频点切换，
并量化评估网络传输提前量、AD9361 锁相环建立时间及槽尾守候预算。
"""
from __future__ import annotations

import ctypes as C
from dataclasses import asdict, dataclass
import time
from typing import Any

from ..sdr.usrp.abi import TuneRequest, TuneResult, UHDTuneRequestPolicy
from ..sdr.usrp.device import USRPDevice


@dataclass(frozen=True)
class AD9361TuningBudget:
    """AD9361 硬件频率切换与时间预算评估。"""

    lead_time_s: float              # 提前下发命令到 FPGA 队列的时间 (>= 2ms)
    pll_lock_settling_s: float      # AD9361 内部 PLL 锁定与模拟滤波器稳定时间 (~300..500µs)
    available_slot_guard_s: float   # 50ms 时隙末尾的可用守候静默时间 (~11ms)
    safe_margin_s: float            # 净安全裕量 (秒)
    is_safe: bool                   # 是否完全落在安全守候期内

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ScheduledHopCommand:
    """已排队的硬件定时跳频指令。"""

    slot_index: int
    frequency_hz: int
    scheduled_hw_time: float
    issued_at_monotonic: float
    mboard: int = 0
    channel: int = 0


class USRPTimedScheduler:
    """USRP N310 硬件定时命令调度器。"""

    def __init__(
        self,
        device: USRPDevice,
        *,
        command_lead_time_s: float = 0.005,      # 提前 5ms 下发命令至 FPGA
        ad9361_settling_s: float = 0.000500,     # AD9361 PLL 建立时间 500µs
        slot_guard_s: float = 0.011,             # 时隙末尾 11ms 守候时间
    ):
        self.device = device
        self.command_lead_time_s = float(command_lead_time_s)
        self.ad9361_settling_s = float(ad9361_settling_s)
        self.slot_guard_s = float(slot_guard_s)
        self.scheduled_hops: list[ScheduledHopCommand] = []

    def evaluate_tuning_budget(self) -> AD9361TuningBudget:
        """评估 AD9361 射频切换时间预算与裕量。"""
        # 总所需时间为网络命令提前量 + 物理锁相环稳定时间
        needed = self.command_lead_time_s + self.ad9361_settling_s
        margin = self.slot_guard_s - needed
        is_safe = (margin > 0.001) and (self.command_lead_time_s >= 0.002)

        return AD9361TuningBudget(
            lead_time_s=self.command_lead_time_s,
            pll_lock_settling_s=self.ad9361_settling_s,
            available_slot_guard_s=self.slot_guard_s,
            safe_margin_s=margin,
            is_safe=is_safe,
        )

    def schedule_rx_hop(
        self,
        target_frequency_hz: int,
        hop_hw_time: float,
        slot_index: int,
        channel: int = 0,
        mboard: int = 0,
    ) -> ScheduledHopCommand:
        """使用 uhd_usrp_set_command_time 在指定的绝对硬件时刻安排 RX 频点切换。"""
        # 1. 设定硬件生效时刻
        self.device.set_command_time(hop_hw_time, mboard=mboard)

        try:
            # 2. 下发调频请求 (由 FPGA 在 hop_hw_time 时刻执行)
            tune_req = TuneRequest(
                target_freq=float(target_frequency_hz),
                rf_freq_policy=UHDTuneRequestPolicy.AUTO,
                rf_freq=0.0,
                dsp_freq_policy=UHDTuneRequestPolicy.AUTO,
                dsp_freq=0.0,
                args=b"",
            )
            tune_res = TuneResult()
            chan = C.c_size_t(channel)
            code = self.device.api.dll.uhd_usrp_set_rx_freq(
                self.device.handle,
                C.byref(tune_req),
                chan,
                C.byref(tune_res),
            )
            self.device.api.check(code, "uhd_usrp_set_rx_freq (timed)")
        finally:
            # 3. 立即清除 command_time，确保后续普通指令不被延迟
            self.device.clear_command_time(mboard=mboard)

        cmd = ScheduledHopCommand(
            slot_index=slot_index,
            frequency_hz=target_frequency_hz,
            scheduled_hw_time=hop_hw_time,
            issued_at_monotonic=time.monotonic(),
            mboard=mboard,
            channel=channel,
        )
        self.scheduled_hops.append(cmd)
        return cmd

    def schedule_tx_hop(
        self,
        target_frequency_hz: int,
        hop_hw_time: float,
        slot_index: int,
        channel: int = 0,
        mboard: int = 0,
    ) -> ScheduledHopCommand:
        """使用 uhd_usrp_set_command_time 在指定的绝对硬件时刻安排 TX 频点切换。"""
        self.device.set_command_time(hop_hw_time, mboard=mboard)
        try:
            tune_req = TuneRequest(
                target_freq=float(target_frequency_hz),
                rf_freq_policy=UHDTuneRequestPolicy.AUTO,
                rf_freq=0.0,
                dsp_freq_policy=UHDTuneRequestPolicy.AUTO,
                dsp_freq=0.0,
                args=b"",
            )
            tune_res = TuneResult()
            chan = C.c_size_t(channel)
            code = self.device.api.dll.uhd_usrp_set_tx_freq(
                self.device.handle,
                C.byref(tune_req),
                chan,
                C.byref(tune_res),
            )
            self.device.api.check(code, "uhd_usrp_set_tx_freq (timed)")
        finally:
            self.device.clear_command_time(mboard=mboard)

        cmd = ScheduledHopCommand(
            slot_index=slot_index,
            frequency_hz=target_frequency_hz,
            scheduled_hw_time=hop_hw_time,
            issued_at_monotonic=time.monotonic(),
            mboard=mboard,
            channel=channel,
        )
        self.scheduled_hops.append(cmd)
        return cmd
