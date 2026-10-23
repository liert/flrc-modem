"""基于硬件时间戳同步的交互式 TDD 双工收发机。

后台持续接收 IQ 采样并动态纠正时钟与跳频同步；
支持上层随时按需投递业务数据（十六进制/字符串），并在精准的 TDD 同步时隙以硬件时间戳发射，同时捕获对端回复。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import queue
import re
from math import isfinite
import threading
import time
from typing import Sequence

import numpy as np

from .live import DEFAULT_DK32S_HOPPING_TABLE, LiveSyncSession
from .state_machine import SyncSnapshot, SyncState
from .tdd import TDDMode, TDDPhase, calculate_flrc_packet_duration
from ..encoding import EncodeContext, MessageTemplate, load_encoder
from ..models import DecodedFrame
from ..modulation import ModulationConfig, modulate_bits
from ..phy.encoder import encode_packet
from ..phy.packet_format import PacketFormat
from ..phy.sync import sync_word_for_polarity
from ..parsing.api import load_parser
from ..models import PhysicalPacket
from ..sdr.usrp.config import USRPConfig
from ..sdr.usrp.device import USRPDevice


@dataclass
class TDDExchangeResult:
    """单次 TDD 发送及接收回复记录。"""

    success: bool
    tx_payload: bytes
    tx_hw_time: float
    tx_slot: int
    tx_frequency_hz: int
    rx_reply: DecodedFrame | None = None
    rtt_seconds: float | None = None
    error: str | None = None
    tx_completed: bool = False
    reply_matched: bool = False
    cancelled: bool = False
    invert_sync: bool = False
    tx_sync_word: bytes | None = None
    reply_invert_sync: bool = False
    reply_sync_word: str | None = None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "tx_payload_hex": self.tx_payload.hex(),
            "tx_hw_time": self.tx_hw_time,
            "tx_slot": self.tx_slot,
            "tx_frequency_hz": self.tx_frequency_hz,
            "rtt_ms": (self.rtt_seconds * 1000) if self.rtt_seconds is not None else None,
            "error": self.error,
            "tx_completed": self.tx_completed,
            "reply_matched": self.reply_matched,
            "cancelled": self.cancelled,
            "invert_sync": self.invert_sync,
            "tx_sync_word_hex": self.tx_sync_word.hex() if self.tx_sync_word is not None else None,
            "reply_invert_sync": self.reply_invert_sync,
            "reply_sync_word_hex": self.reply_sync_word,
        }
        if self.rx_reply:
            d["rx_reply"] = {
                "time_s": self.rx_reply.time_s,
                "frequency_hz": self.rx_reply.frequency_hz,
                "payload_hex": self.rx_reply.packet.payload.hex(),
                "sample_power_db": self.rx_reply.sample_power_db,
            }
        return d


@dataclass
class PendingReply:
    start: float
    end: float
    frequency_hz: int
    sync_word: bytes
    tx_payload: bytes
    command: int
    sequence: int
    box: queue.Queue
    independent_sequence: bool = False
    previous_air_sequence: int | None = None


class LiveTDDTransceiver:
    """实时 TDD 同步双工收发机。"""

    def __init__(
        self,
        *,
        sdr_type: str = "usrp",
        usrp_args: str = "addr=192.168.10.2",
        frequency_hz: int = 2471660000,
        sample_rate: float | None = None,
        master_clock_rate: float | None = None,
        gain: float = 30.0,
        tx_gain: float = 30.0,
        channel: int = 0,
        antenna: str = "RX2",
        sync_word: str | None = None,
        tx_invert_sync: bool = False,
        reply_invert_sync: bool = False,
        bit_rate: float = 325_000.0,
        coding_rate: str = "3/4",
        bt: float = 1.0,
        packet_format: PacketFormat | None = None,
        amplitude: float = 0.7,
        ramp_time_us: float = 12.0,
        protocol: str | None = "dk32s",
        hopping_table: Sequence[int] | None = None,
        tdd_mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        timed_hopping: bool = False,
        usrp_device: USRPDevice | None = None,
    ):
        self.sdr_type = sdr_type.lower()
        if protocol == 'datalink':
            raise ValueError('DataLink 尚未实现同步时隙模型，请使用 transmit 盲发或 exchange 双机交互')
        if self.sdr_type != "usrp":
            raise ValueError("Hardware-timed duplex requires the USRP backend")
        self.usrp_args = usrp_args
        self.frequency_hz = int(frequency_hz)
        self.master_clock_rate = master_clock_rate
        self.gain = float(gain)
        self.tx_gain = float(tx_gain)
        self.channel = int(channel)
        self.sync_word = sync_word.lower() if sync_word else None
        if type(tx_invert_sync) is not bool or type(reply_invert_sync) is not bool:
            raise ValueError('收发同步字反转选项必须为布尔值')
        if reply_invert_sync and not self.sync_word:
            raise ValueError('回复反转需要明确的基准同步字，请指定 --sync 或射频配置中的同步字')
        self.tx_invert_sync, self.reply_invert_sync = tx_invert_sync, reply_invert_sync
        # 收端同时接受原字与反字：地面帧用基准字，天空端回复可能使用反字。
        # 逐条报文只在匹配阶段选择期望的回复字，不改变解调候选项。
        self.rx_sync_words = tuple(dict.fromkeys(filter(None, (
            self.sync_word, sync_word_for_polarity(self.sync_word, True)))))
        self.bit_rate = float(bit_rate)
        self.coding_rate = coding_rate
        self.bt = bt
        self.packet_format = packet_format or PacketFormat()
        self.amplitude, self.ramp_time_us = amplitude, ramp_time_us
        self.protocol = protocol
        self.hopping_table = list(hopping_table or DEFAULT_DK32S_HOPPING_TABLE)
        self.tdd_mode = tdd_mode
        self.timed_hopping = timed_hopping

        # 协议编码器（支持直接将 JSON 字典编码为 FLRC 物理载荷）
        self._encoder = load_encoder(protocol) if protocol else None
        self._parser = load_parser(protocol)
        self._tx_frame_counter: int = 0

        # 硬件句柄（单设备收发复用）
        self._custom_dev = usrp_device
        self.device = usrp_device or USRPDevice(device_args=self.usrp_args, master_clock_rate=self.master_clock_rate)
        self._owns_device = usrp_device is None

        # 实时同步会话（负责持续 RX 采样与时钟校准）
        self.session = LiveSyncSession(
            sdr_type=self.sdr_type,
            usrp_args=self.usrp_args,
            frequency_hz=self.frequency_hz,
            sample_rate=sample_rate,
            master_clock_rate=self.master_clock_rate,
            gain=self.gain,
            channel=self.channel,
            antenna=antenna,
            sync_word=self.sync_word,
            sync_words=self.rx_sync_words[1:],
            hopping_table=self.hopping_table,
            tdd_mode=self.tdd_mode,
            timed_hopping=self.timed_hopping,
            usrp_device=self.device,
            protocol=protocol,
            bit_rate=self.bit_rate,
            coding_rate=self.coding_rate,
            bt=self.bt,
            # --payload-length describes TX. A fixed-header reply can carry a
            # different payload length, which the receiver must recover by CRC.
            packet_format=replace(self.packet_format, payload_lengths=()),
        )

        # 状态分发与回复等待队列
        self.last_snapshot: SyncSnapshot | None = None
        self._pending_waiters: list[PendingReply] = []
        self._waiter_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._last_air_sequence: int | None = None
        self._last_air_time = float("-inf")
        self._dispatcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """打开设备并启动后台持续接收与同步调度线程。"""
        try:
            if not self.device.handle.value:
                self.device.open()

            # Initialize TX before RX hopping starts; slow RF setup must not block
            # the command queue just as a hop deadline approaches.
            self.device.prepare_transmit(self._tx_config(self.frequency_hz))

            # 启动后台流式接收会话
            self.session.start()

            # 启动事件分发器线程
            self._stop_event.clear()
            self._dispatcher_thread = threading.Thread(
                target=self._dispatch_loop,
                name="flrc-tdd-dispatcher",
                daemon=True,
            )
            self._dispatcher_thread.start()
        except BaseException:
            self.stop()
            raise

    def _tx_config(self, frequency: int) -> USRPConfig:
        return USRPConfig(frequency_hz=float(frequency), sample_rate=self.session.sample_rate,
                          gain=self.tx_gain, channel=self.channel, cpu_format="sc8",
                          master_clock_rate=self.master_clock_rate,
                          lo_frequency_hz=2450060000 if self.timed_hopping else None,
                          bandwidth_hz=100000000 if self.timed_hopping else None)

    def stop(self):
        """停止收发机并释放资源。"""
        self._stop_event.set()
        self.session.stop()

        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=1.0)

        if self._owns_device:
            self.device.close()

    def request_stop(self):
        """Interrupt pending waits; the owning context closes hardware after TX returns."""
        self._stop_event.set()

    def _dispatch_loop(self):
        """持续消费 session 事件，更新最新同步快照并唤醒等待回复的发送方。"""
        for frame, snap in self.session.events():
            if self._stop_event.is_set():
                break
            self.last_snapshot = snap

            # 匹配回复帧
            t_rx = frame.time_s
            with self._waiter_lock:
                matched = []
                for waiter in self._pending_waiters:
                    if self._matches_reply(frame, waiter):
                        try:
                            waiter.box.put_nowait(frame)
                        except queue.Full:
                            pass
                        matched.append(waiter)
                for m in matched:
                    self._pending_waiters.remove(m)
                if self._parser is not None and t_rx >= self._last_air_time:
                    try:
                        app = self._parser(frame) or {}
                    except (ValueError, TypeError, IndexError):
                        app = {}
                    if app.get("direction") == "air_to_ground" and app.get("sum8_ok") is True:
                        self._last_air_sequence = app.get("sequence")
                        self._last_air_time = t_rx

    def _matches_reply(self, frame: DecodedFrame, waiter: PendingReply) -> bool:
        if (not waiter.start <= frame.time_s <= waiter.end
                or abs(frame.frequency_hz - waiter.frequency_hz) > 1
                or frame.packet.sync_word != waiter.sync_word
                or frame.packet.payload == waiter.tx_payload or self._parser is None):
            return False
        try:
            app = self._parser(frame) or {}
        except (ValueError, TypeError, IndexError):
            return False
        if not (app.get("direction") == "air_to_ground" and app.get("sum8_ok") is True
                and app.get("command") == waiter.command):
            return False
        sequence = app.get("sequence")
        if waiter.independent_sequence:
            # DK32S air packets have their own 15-bit TX counter, even when
            # is_ack=True. Real query replies do not echo the ground counter.
            if not isinstance(sequence, int):
                return False
            if waiter.command in (0x11, 0x16, 0x46):
                # The normal controller can trigger indistinguishable periodic
                # telemetry. Do not label it as this request's verified reply.
                return False
            return (waiter.previous_air_sequence is None
                    or 0 < (sequence - waiter.previous_air_sequence) % 32768 < 16384)
        return sequence == waiter.sequence

    def wait_locked(self, timeout: float = 5.0, *, cancel_event: threading.Event | None = None) -> bool:
        """阻塞等待同步状态达到 LOCKED 或 HOLDOVER。"""
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("同步等待时间必须为有限正数（秒）")
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self._stop_event.is_set() or (cancel_event is not None and cancel_event.is_set()) or self.session.stats.error:
                return False
            with self.session.state_lock:
                snap = self.session.state_machine.predict(self.device.get_time_estimate())
                if snap.state in (SyncState.LOCKED, SyncState.HOLDOVER) and self.session.clock_ready_for_transmit:
                    return True
            time.sleep(0.010)
        return False

    def sync_diagnostics(self) -> dict:
        """Explain why RF activity has not yet produced a usable clock fit."""
        with self.session.state_lock:
            metrics = self.session.realtime_metrics()
            sm = self.session.state_machine
            now = self.device.get_time_estimate()
            self.session._recover_backlog(now)
            snap = sm.predict(now)
            anchors = list(sm.fhss.anchors)
            return {**metrics, **snap.to_dict(), "state": snap.state.value, "slot": snap.slot_index,
                    "frequency_hz": snap.frequency_hz, "clock_drift_ppm": snap.clock_drift_ppm,
                    "sample_rate": self.session.sample_rate, "sync_word": sm.sync_word,
                    "base_sync_word": self.sync_word,
                    "accepted_sync_words": sorted(sm.accepted_sync_words),
                    "tx_invert_sync": self.tx_invert_sync,
                    "reply_invert_sync": self.reply_invert_sync,
                    "reply_sync_word": sync_word_for_polarity(self.sync_word, self.reply_invert_sync),
                    "decoded_frames": self.session.stats.decoded_frames,
                    "anchor_count": len(anchors),
                    "anchor_span_s": anchors[-1][0] - anchors[0][0] if len(anchors) > 1 else 0.0,
                    "overflow_count": self.session.stats.overflow_count,
                    "receive_frequency_hz": self.session.receive_frequency_at(now),
                    "reacquiring": self.session._reacquiring,
                    "reacquisition_count": self.session.stats.reacquisition_count,
                    "frame_counts": dict(sm.frame_counts),
                    "rejection_counts": dict(sm.rejection_counts),
                    "accepted_anchors_total": sm.fhss.accepted_count,
                    "last_rejection": sm.last_rejection,
                    "last_parser_error": sm.last_parser_error,
                    "loss_reason": sm.loss_reason,
                    "error": self.session.stats.error}

    def _plan_window(self, now: float, duration: float, *, target_hw_time=None, target_frequency_hz=None,
                     reply_duration: float | None = None):
        """Find an idle subframe that also leaves time to receive a reply."""
        t = float(target_hw_time) if target_hw_time is not None else now + 0.150
        if t < now + 0.100:
            raise ValueError("指定发送时间已过期或提前量不足 100ms")
        limit = now + self.session.state_machine.fhss.cycle_seconds * 2 + 0.1
        while t < limit:
            with self.session.state_lock:
                sm = self.session.state_machine
                pred = sm.predict(t)
                if pred.state not in (SyncState.LOCKED, SyncState.HOLDOVER) or not self.session.clock_ready_for_transmit:
                    raise ValueError("当前硬件时刻的同步已失效，拒绝使用旧快照发送")
                window = sm.tdd.classify_phase(t, pred.slot_start_hw_time, sm.last_ground_tx_hw)
                budget = duration + sm.tdd.estimated_turnaround_s + (reply_duration or sm.tdd.air_duration_s) + 0.0005
                margin = max(0.0002, pred.uncertainty_s + 0.0001)
            f = pred.frequency_hz
            rx_ok = self.session.receive_frequency_at(t) == f
            end = min(window.end_hw_time, pred.slot_end_hw_time - sm.tdd.slot_guard_s)
            candidate = max(t, window.start_hw_time + margin) if target_hw_time is None else t
            if (window.phase == TDDPhase.IDLE and candidate >= window.start_hw_time + margin
                    and candidate + budget + margin <= end and rx_ok
                    and (target_frequency_hz is None or target_frequency_hz == f)):
                return candidate, f, pred.slot_index, end - margin
            if target_hw_time is not None:
                raise ValueError("指定时刻不在可用 TDD 空闲窗口，或 RX 不在目标频点")
            if not rx_ok or (target_frequency_hz is not None and target_frequency_hz != f):
                t = pred.next_hop_hw_time + 0.005
            elif window.phase == TDDPhase.SLOT_GUARD or t + budget > pred.slot_end_hw_time - sm.tdd.slot_guard_s:
                t = pred.next_hop_hw_time + 0.005
            else:
                t = max(t + 0.0001, window.end_hw_time + 0.0001)
        raise ValueError("没有找到 RX 同频且足以容纳发送和回复的 TDD 窗口")

    def send_and_wait_reply(self, payload: bytes | str | dict, **kwargs) -> TDDExchangeResult:
        with self._send_lock:
            return self._send_and_wait_reply(payload, **kwargs)

    def _send_and_wait_reply(
        self,
        payload: bytes | str | dict,
        *,
        wait_reply: bool = True,
        reply_timeout: float = 1.0,
        sync_timeout: float = 3.0,
        target_hw_time: float | None = None,
        target_frequency_hz: int | None = None,
        cancel_event: threading.Event | None = None,
        invert_sync: bool | None = None,
        reply_invert_sync: bool | None = None,
    ) -> TDDExchangeResult:
        """在同步锁定的 TDD 时隙准时打出数据，并等待接收回复。

        参数:
            payload: 发送载荷（bytes、十六进制字符串如 '010203040506' 或协议 JSON dict）
            wait_reply: 是否等待对端回复
            reply_timeout: 等待回复超时（秒）
            sync_timeout: 等待同步锁定超时（秒）
            target_hw_time: 显式指定的硬件发射时刻（默认由状态机自动选定最佳发射窗口）
            target_frequency_hz: 显式指定的发射载频（默认按跳频状态机选定）
            invert_sync: 本条发送的同步字逐位取反；None 使用会话发送默认值
            reply_invert_sync: 本条等待的回复使用取反后的同步字；None 使用会话回复默认值
        """
        if invert_sync is None:
            invert_sync = self.tx_invert_sync
        if reply_invert_sync is None:
            reply_invert_sync = self.reply_invert_sync
        if type(invert_sync) is not bool or type(reply_invert_sync) is not bool:
            raise ValueError('收发同步字反转选项必须为布尔值')
        tx_sync_bytes = None
        # 回复字基于基准字（未配置时用空口恢复出的字），与发送极性无关。
        reply_base = self.sync_word or self.session.state_machine.sync_word
        if reply_invert_sync and not self.sync_word:
            raise ValueError('回复反转需要明确的基准同步字，请指定 --sync 或射频配置中的同步字')
        reply_sync_text = sync_word_for_polarity(reply_base, reply_invert_sync) if reply_base else None

        def exchange_result(*args, **kwargs):
            return TDDExchangeResult(*args, **kwargs, invert_sync=invert_sync, tx_sync_word=tx_sync_bytes,
                                     reply_invert_sync=reply_invert_sync, reply_sync_word=reply_sync_text)

        # 1. 规范化载荷（统一支持 bytes、十六进制字符串与协议 JSON 字典）
        encode_ctx = EncodeContext(
            frame_index=self._tx_frame_counter,
            start_sample=0,
            sample_rate=self.session.sample_rate,
        )
        if isinstance(payload, dict):
            if self._encoder is None:
                raise ValueError(f"发送协议业务字段必须配置有效的 --protocol (当前为 None)")
            raw_payload = MessageTemplate(self._encoder, payload).encode(encode_ctx)
            self._tx_frame_counter += 1
        elif isinstance(payload, str):
            trimmed = payload.strip()
            if trimmed.startswith("{") and trimmed.endswith("}"):
                import json
                if self._encoder is None:
                    raise ValueError(f"发送协议 JSON 报文必须配置有效的 --protocol (当前为 None)")
                msg_dict = json.loads(trimmed)
                payload = msg_dict
                raw_payload = MessageTemplate(self._encoder, msg_dict).encode(encode_ctx)
                self._tx_frame_counter += 1
            else:
                clean = re.sub(r"[\s,]+", "", trimmed)
                try:
                    raw_payload = bytes.fromhex(clean)
                except ValueError:
                    raw_payload = trimmed.encode("utf-8")
        else:
            raw_payload = bytes(payload)

        # 保证 FLRC 物理载荷在 6..127 字节
        if len(raw_payload) < 6:
            raw_payload = raw_payload.ljust(6, b"\x00")
        elif len(raw_payload) > 127:
            raise ValueError(f"物理载荷长度 {len(raw_payload)} 超过 FLRC 上限 127 字节")

        # 2. 检查同步锁定状态
        if not self.wait_locked(timeout=sync_timeout, **({'cancel_event': cancel_event} if cancel_event is not None else {})):
            if cancel_event is not None and cancel_event.is_set():
                return exchange_result(False, raw_payload, 0, 0, self.frequency_hz,
                                         error='编排已停止，取消尚未提交的发送', cancelled=True)
            curr_state = self.last_snapshot.state.value if self.last_snapshot else "unknown"
            return exchange_result(
                success=False,
                tx_payload=raw_payload,
                tx_hw_time=0.0,
                tx_slot=0,
                tx_frequency_hz=self.frequency_hz,
                error=f"同步未锁定 (当前状态: {curr_state})，请等待接收到基准帧以校准时钟与时隙",
            )

        # Prepare waveform before selecting any hardware deadline.
        tx_sync = self.sync_word or self.session.state_machine.sync_word
        if not tx_sync:
            return exchange_result(False, raw_payload, 0.0, 0, self.frequency_hz,
                                     error="未指定同步字且未从接收信号中恢复出有效同步字")
        tx_sync_bytes = bytes.fromhex(sync_word_for_polarity(tx_sync, invert_sync))
        if reply_sync_text is None:
            reply_sync_text = sync_word_for_polarity(tx_sync, reply_invert_sync)
        reply_sync_bytes = bytes.fromhex(reply_sync_text)

        def modulate(payload_bytes):
            if self.packet_format.header_type == 'fixed' and self.packet_format.payload_lengths and len(payload_bytes) not in self.packet_format.payload_lengths:
                raise ValueError('实际载荷不符合会话固定包长，请重新配置会话')
            packet = encode_packet(payload_bytes, sync_word=tx_sync_bytes, coding_rate=self.coding_rate,
                                   header_type=self.packet_format.header_type, crc_bytes=self.packet_format.crc_bytes,
                                   crc_initial=self.packet_format.crc_initial)
            cfg = ModulationConfig(sample_rate=self.session.sample_rate, bit_rate=self.bit_rate,
                                   coding_rate=self.coding_rate, bt=self.bt, offset_hz=0,
                                   amplitude=self.amplitude, ramp_time_us=self.ramp_time_us)
            wave = modulate_bits(packet.bits, cfg)
            return np.column_stack((
                np.clip(np.rint(wave.real * 127), -128, 127).astype(np.int8),
                np.clip(np.rint(wave.imag * 127), -128, 127).astype(np.int8),
            )).tobytes()

        def parse_payload(payload_bytes):
            if self._parser is None:
                return {}
            frame = DecodedFrame(0, self.frequency_hz, 0, 0, 0, 0,
                                 PhysicalPacket(tx_sync_bytes, b"", payload_bytes, b"", 0))
            try:
                return self._parser(frame) or {}
            except (ValueError, TypeError, IndexError):
                return {}

        tx_cs8 = modulate(raw_payload)
        duration = len(tx_cs8) / 2 / self.session.sample_rate
        tx_app = parse_payload(raw_payload)
        if wait_reply and (tx_app.get("direction") != "ground_to_air" or not tx_app.get("sum8_ok")):
            return exchange_result(False, raw_payload, 0, 0, self.frequency_hz,
                                     error="等待匹配回复需要可解析的地面协议载荷（命令和序号）")
        t_tx, f_tx, s_tx = 0.0, self.frequency_hz, 0
        reply_box: queue.Queue[DecodedFrame] = queue.Queue(maxsize=1)
        try:
            for attempt in range(4):
                if cancel_event is not None and cancel_event.is_set():
                    return exchange_result(False, raw_payload, 0, 0, self.frequency_hz,
                                             error='编排已停止，取消尚未提交的发送', cancelled=True)
                now = self.device.get_time_estimate()
                t_tx, f_tx, s_tx, reply_end = self._plan_window(
                    now, duration, target_hw_time=target_hw_time, target_frequency_hz=target_frequency_hz,
                    reply_duration=calculate_flrc_packet_duration(26 if tx_app.get("command") == 0x23 else 7,
                                                                  int(self.bit_rate), self.coding_rate))
                tx_config = self._tx_config(f_tx)
                actual_rate = self.device.prepare_transmit(tx_config)
                if abs(actual_rate - self.session.sample_rate) > 1:
                    raise ValueError("TX 与 RX 实际采样率不同，无法保证调制和时序")
                # A structured ground message defaults to the recovered remote
                # clock, not a zero timestamp derived from start_sample=0.
                if isinstance(payload, dict) and "timestamp_ticks" not in payload and tx_app.get("direction") == "ground_to_air":
                    message = dict(payload)
                    with self.session.state_lock:
                        message["timestamp_ticks"] = self.session.state_machine.predict(t_tx).timestamp_ticks
                    message.setdefault("sequence", tx_app["sequence"])
                    # Replanning must reuse this request's frame index, so
                    # counter/cycle rules do not advance during preview/retry.
                    raw_payload = MessageTemplate(self._encoder, message).encode(encode_ctx)
                    tx_cs8 = modulate(raw_payload)
                if t_tx - self.device.get_time_estimate() >= 0.100:
                    break
                if target_hw_time is not None:
                    raise ValueError("硬件准备完成时指定发送时间已过期")
            else:
                raise ValueError("硬件准备耗时超过提前量，无法提交准时发送")
            if wait_reply:
                # 回复匹配使用本条请求的回复字，与发送极性无关。
                waiter = PendingReply(t_tx + duration + 0.0002,
                                      min(reply_end, t_tx + duration + 0.006),
                                      f_tx, reply_sync_bytes, raw_payload, tx_app["command"],
                                      tx_app["sequence"], reply_box)
                with self._waiter_lock:
                    waiter.independent_sequence = bool(self.protocol and self.protocol.startswith("dk32s"))
                    waiter.previous_air_sequence = self._last_air_sequence
                    self._pending_waiters.append(waiter)
            self.session.exclude_transmission(t_tx, t_tx + duration, raw_payload)
            if cancel_event is not None and cancel_event.is_set():
                with self._waiter_lock:
                    self._pending_waiters = [w for w in self._pending_waiters if w.box is not reply_box]
                return exchange_result(False, raw_payload, t_tx, s_tx, f_tx,
                                         error='编排已停止，取消尚未提交的发送', cancelled=True)
            if self._stop_event.is_set():
                raise ValueError('会话正在停止，取消尚未提交的发送')
            sent = self.device.transmit_burst(tx_cs8, tx_config, target_hw_time_s=t_tx, timeout=2.0)
            if sent != len(tx_cs8) // 2:
                raise ValueError(f"硬件仅提交了 {sent}/{len(tx_cs8)//2} 个发送样本")
        except Exception as error:
            with self._waiter_lock:
                self._pending_waiters = [w for w in self._pending_waiters if w.box is not reply_box]
            return exchange_result(False, raw_payload, t_tx, s_tx, f_tx,
                                     error=f"发送失败: {type(error).__name__}: {error}")
        if not wait_reply:
            return exchange_result(True, raw_payload, t_tx, s_tx, f_tx, tx_completed=True)
        try:
            deadline = time.monotonic() + reply_timeout
            while True:
                if self._stop_event.is_set() or (cancel_event is not None and cancel_event.is_set()):
                    return exchange_result(False, raw_payload, t_tx, s_tx, f_tx,
                                             error='编排或会话已停止', tx_completed=True, cancelled=True)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                try:
                    reply = reply_box.get(timeout=min(.1, remaining))
                    break
                except queue.Empty:
                    continue
            return exchange_result(True, raw_payload, t_tx, s_tx, f_tx,
                                     rx_reply=reply, rtt_seconds=reply.time_s - t_tx,
                                     tx_completed=True, reply_matched=True)
        except queue.Empty:
            return exchange_result(False, raw_payload, t_tx, s_tx, f_tx,
                                     error="硬件发送已完成，等待匹配的对端回复超时", tx_completed=True)
        finally:
            with self._waiter_lock:
                self._pending_waiters = [w for w in self._pending_waiters if w.box is not reply_box]
