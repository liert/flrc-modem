"""有限 DK32S 跳频测试计划：预生成 IQ，主机按时隙启动短 TX，独立 RX 持续采集。"""
from dataclasses import dataclass
from copy import deepcopy
import io
from math import isfinite
import time

import numpy as np

from .encoding import EncodeContext, MessageTemplate, load_encoder
from .io.iq import encode_samples
from .modulation import modulate_bits
from .phy.encoder import encode_packet
from .sdr.hackrf import HackRFConfig, StreamCancelled
from .sync.live import parse_rf_config


@dataclass(frozen=True)
class HopBurst:
    slot: int
    start_seconds: float
    frequency_hz: int
    iq: bytes


@dataclass(frozen=True)
class HoppingPlan:
    bursts: tuple[HopBurst, ...]
    sent_packets: frozenset[tuple[str, str]]
    sample_rate: float
    offset_hz: float
    dwell_seconds: float
    packet_count: int

    def transmit(self, device, cancel, *, tx_gain=24, correction_hz=0, slot_stats=None):
        """配置/USB 启动有主机抖动，记录每个时隙迟到量，不声称硬件定时精度。"""
        if not isfinite(correction_hz):
            raise ValueError("频率校正必须为有限数")
        configs = [HackRFConfig(round(b.frequency_hz - self.offset_hz + correction_hz),
                                sample_rate=self.sample_rate, tx_gain=tx_gain) for b in self.bursts]
        slots = [] if slot_stats is None else slot_stats
        anchor = time.monotonic() + 0.05
        for burst, config in zip(self.bursts, configs):
            deadline = anchor + burst.start_seconds
            if cancel.wait(max(0, deadline - time.monotonic())):
                raise StreamCancelled("跳频计划被取消")
            begin = time.monotonic()
            late = begin - deadline
            # 严重迟到后仍发送过期时间戳，会把接收机同步到错误的频点。
            duration = len(burst.iq) / 2 / self.sample_rate
            if late + duration >= self.dwell_seconds:
                slots.append({"slot": burst.slot, "frequency_hz": burst.frequency_hz,
                              "late_ms": late * 1000, "elapsed_ms": 0,
                              "status": "skipped_late", "stream": None})
                continue
            entry = {"slot": burst.slot, "frequency_hz": burst.frequency_hz,
                     "late_ms": late * 1000, "elapsed_ms": 0, "status": "running", "stream": None}
            slots.append(entry)
            try:
                device.transmit(io.BytesIO(burst.iq), config, cancel_event=cancel,
                                queue_depth=8, prefill_blocks=4, timeout=self.dwell_seconds + 2)
                entry["status"] = "completed"
            except BaseException as error:
                entry["status"] = "cancelled" if isinstance(error, (KeyboardInterrupt, StreamCancelled)) else "failed"
                entry["error"] = f"{type(error).__name__}: {error}"
                raise
            finally:
                entry["elapsed_ms"] = (time.monotonic() - begin) * 1000
                if device.last_stats is not None:
                    entry["stream"] = device.last_stats.to_dict()
        return {"slots": slots, "packet_count": self.packet_count,
                "skipped_slots": sum(s["status"] == "skipped_late" for s in slots),
                "max_late_ms": max(s["late_ms"] for s in slots),
                "note": "主机调度时隙；late_ms 从调用前测量，不含配置和 USB 到天线的延迟"}


def prepare_hopping_plan(message, radio, modulation, *, cycles=3, dwell_seconds=0.05,
                         packets_per_slot=3, packet_interval=0.012, guard_seconds=0.010,
                         tail_seconds=0.002):
    """radio 使用 get_rf_channels.py --json 的结果，不猜测厂商信道编号。"""
    if not isinstance(cycles, int) or isinstance(cycles, bool) or cycles < 1:
        raise ValueError("cycles 必须为正整数")
    if not isinstance(packets_per_slot, int) or isinstance(packets_per_slot, bool) or packets_per_slot < 1:
        raise ValueError("packets_per_slot 必须为正整数")
    for name, value in (("dwell_seconds", dwell_seconds), ("packet_interval", packet_interval),
                        ("guard_seconds", guard_seconds), ("tail_seconds", tail_seconds)):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} 必须为有限正数")
    # 当前被动捕获和固件证据仅建立了20×50ms的模型，其他协议不能套用该时间戳规则。
    if dwell_seconds != 0.05:
        raise ValueError("当前 DK32S 时间戳模型仅验证了 50ms 时隙")
    minimum_seconds = guard_seconds + (packets_per_slot - 1) * packet_interval + tail_seconds
    if minimum_seconds >= dwell_seconds - 0.005:
        raise ValueError("单时隙的包间隔/保护时间已经超过可发送窗口")
    if minimum_seconds * modulation.sample_rate * 2 * cycles * 20 > 128 * 1024 * 1024:
        raise ValueError("跳频预生成波形至少超过128MiB，请减少 cycles/packets_per_slot/采样率")
    frequencies, sync_hex = parse_rf_config(radio)
    if sync_hex is None or frequencies is None or len(frequencies) != 20:
        raise ValueError("射频配置必须包含4B同步字和20个跳频点")
    sync = bytes.fromhex(sync_hex)
    for frequency in frequencies:
        HackRFConfig(frequency, sample_rate=modulation.sample_rate)
    if message.get("direction") != "ground_to_air":
        raise ValueError("跳频测试必须发送 ground_to_air 模板")
    message = deepcopy(message)
    message["timestamp_ticks"] = 0
    updates = message.setdefault("updates", {})
    updates["timestamp_ticks"] = {"type": "clock", "rate": 10000, "modulo": 0x100000000}
    template = MessageTemplate(load_encoder("dk32s"), message)
    plan, sent, total_bytes = [], set(), 0
    rate = modulation.sample_rate
    for slot in range(cycles * 20):
        position = round(guard_seconds * rate)
        chunks = [np.zeros(position, dtype=np.complex64)]
        for index in range(packets_per_slot):
            context = EncodeContext(slot * packets_per_slot + index,
                                    round(slot * dwell_seconds * rate) + position + modulation.ramp_samples, rate)
            payload = template.encode(context)
            packet = encode_packet(payload, sync, coding_rate=modulation.coding_rate)
            waveform = modulate_bits(packet.bits, modulation)
            carrier = np.exp(2j * np.pi * modulation.offset_hz * (position + np.arange(len(waveform))) / rate)
            chunks.append((waveform * carrier).astype(np.complex64))
            position += len(waveform)
            sent.add((sync.hex(), payload.hex()))
            if index + 1 < packets_per_slot:
                gap = round(packet_interval * rate) - len(waveform)
                if gap < 0:
                    raise ValueError("报文超过 packet_interval")
                chunks.append(np.zeros(gap, dtype=np.complex64))
                position += gap
        chunks.append(np.zeros(round(tail_seconds * rate), dtype=np.complex64))
        raw = b"".join(encode_samples(chunk, "cs8") for chunk in chunks)
        if len(raw) / 2 / rate >= dwell_seconds - 0.005:
            raise ValueError("单时隙波形过长，必须为配置与 USB 调度预留至少5ms")
        total_bytes += len(raw)
        if total_bytes > 128 * 1024 * 1024:
            raise ValueError("跳频预生成波形超过128MiB，请减少 cycles/packets_per_slot/采样率")
        plan.append(HopBurst(slot, slot * dwell_seconds, frequencies[slot % 20], raw))
    return HoppingPlan(tuple(plan), frozenset(sent), rate, modulation.offset_hz, dwell_seconds,
                       cycles * 20 * packets_per_slot)
