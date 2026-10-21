"""Ordered UHD timed tuning. N310 uses AD9371; settling must be measured."""
from __future__ import annotations

import ctypes as C
from collections import deque
from dataclasses import asdict, dataclass
from math import isfinite
import time

from ..sdr.usrp.abi import TuneRequest, TuneResult, UHDTuneRequestPolicy
from ..sdr.usrp.device import USRPDevice


@dataclass(frozen=True)
class RFTuningBudget:
    lead_time_s: float
    pll_lock_settling_s: float
    available_slot_guard_s: float
    safe_margin_s: float
    is_safe: bool
    settling_measured: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# Backward-compatible import; this name no longer implies an N310 RFIC type.
AD9361TuningBudget = RFTuningBudget


class LateTuneError(ValueError):
    """No command was submitted; the caller can plan another future boundary."""


@dataclass(frozen=True)
class ScheduledHopCommand:
    slot_index: int
    frequency_hz: int
    scheduled_hw_time: float
    issued_at_monotonic: float
    mboard: int = 0
    channel: int = 0


class USRPTimedScheduler:
    def __init__(self, device: USRPDevice, *, command_lead_time_s: float = 0.100,
                 settling_s: float = 0.005, settling_measured: bool = False,
                 slot_guard_s: float = 0.011, ad9361_settling_s: float | None = None,
                 digital_only: bool = False):
        self.device = device
        self.command_lead_time_s = float(command_lead_time_s)
        self.settling_s = float(settling_s if ad9361_settling_s is None else ad9361_settling_s)
        self.slot_guard_s = float(slot_guard_s)
        self.settling_measured = bool(settling_measured)
        self.digital_only = digital_only
        if any(not isfinite(x) or x <= 0 for x in
               (self.command_lead_time_s, self.settling_s, self.slot_guard_s)):
            raise ValueError("Timing budgets must be finite and positive")
        self.scheduled_hops = deque(maxlen=256)
        self._last = {}

    def evaluate_tuning_budget(self) -> RFTuningBudget:
        # Network lead runs before the tuning instant, not after it.
        margin = self.slot_guard_s - self.settling_s
        return RFTuningBudget(self.command_lead_time_s, self.settling_s,
                              self.slot_guard_s, margin,
                              self.settling_measured and margin > 0.001,
                              self.settling_measured)

    def _schedule(self, direction, target_frequency_hz, hop_hw_time, slot_index, channel, mboard):
        if not isfinite(hop_hw_time) or target_frequency_hz <= 0:
            raise ValueError("Invalid tune time or frequency")
        key = (direction, channel, mboard)
        # UHD command time is shared mutable state. Keep set/tune/clear atomic
        # with respect to all other RF configuration on this device.
        with self.device.command_lock:
            last = self._last.get(key)
            if last and abs(last.scheduled_hw_time - hop_hw_time) < 1e-7 and last.frequency_hz == target_frequency_hz:
                return last
            if last and hop_hw_time <= last.scheduled_hw_time:
                raise ValueError("Timed tune commands must be strictly ordered")
            if hop_hw_time - self.device.get_time_estimate() < self.command_lead_time_s:
                raise LateTuneError("Timed tune deadline is too close or already past")
            self.device.set_command_time(hop_hw_time, mboard=mboard)
            try:
                policy = UHDTuneRequestPolicy.NONE if self.digital_only else UHDTuneRequestPolicy.AUTO
                request = TuneRequest(float(target_frequency_hz), policy, 0.0,
                                      UHDTuneRequestPolicy.AUTO, 0.0, b"")
                result = TuneResult()
                func = getattr(self.device.api.dll, f"uhd_usrp_set_{direction}_freq")
                self.device.api.check(func(self.device.handle, C.byref(request), C.c_size_t(channel),
                                           C.byref(result)), f"set_{direction}_freq (timed)")
            finally:
                self.device.clear_command_time(mboard=mboard)
            cmd = ScheduledHopCommand(slot_index, target_frequency_hz, hop_hw_time,
                                      time.monotonic(), mboard, channel)
            self._last[key] = cmd
            self.scheduled_hops.append(cmd)
            return cmd

    def schedule_rx_hop(self, target_frequency_hz, hop_hw_time, slot_index, channel=0, mboard=0):
        return self._schedule("rx", target_frequency_hz, hop_hw_time, slot_index, channel, mboard)

    def schedule_tx_hop(self, target_frequency_hz, hop_hw_time, slot_index, channel=0, mboard=0):
        return self._schedule("tx", target_frequency_hz, hop_hw_time, slot_index, channel, mboard)

    def schedule_rx_reacquisition(self, frequency_hz, slot_index=0, channel=0, mboard=0):
        """排在已提交的 RX 调谐之后停驻观测信道；clear_command_time 不会清空 FPGA 队列。"""
        with self.device.command_lock:
            now = self.device.get_time_estimate()
            last = self._last.get(('rx', channel, mboard))
            when = now + 2 * self.command_lead_time_s + self.settling_s
            if last:
                when = max(when, last.scheduled_hw_time + self.settling_s)
            return self.schedule_rx_hop(frequency_hz, when, slot_index, channel, mboard)
