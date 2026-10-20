"""有界报文队列到连续 IQ；等待报文时补静默，避免阻塞 USB 生产线程。"""
from collections import deque
from dataclasses import dataclass
from math import isfinite, pi
import threading
import time

import numpy as np

from ..modulation import ModulationConfig, modulate_bits
from ..encoding import EncodeContext, MessageTemplate
from ..modulation.waveform import _silence
from ..phy.encoder import EncodedPacket, encode_packet
from ..phy.packet_format import PacketFormat
from ..sdr.hackrf import IQIterableSource, StreamCancelled


@dataclass(frozen=True)
class _Request:
    packet: EncodedPacket | None
    repeat: int
    template: MessageTemplate | None = None
    sync_word: bytes | None = None


class PacketSource:
    def __init__(self, config: ModulationConfig, sync_word=None, crc_initial=None, *, depth=64, header_type="variable", crc_bytes=3, payload_lengths=()):
        if not isinstance(depth, int) or not 1 <= depth <= 4096:
            raise ValueError("报文队列深度必须为 1..4096")
        if config.iq_format != "cs8":
            raise ValueError("实时发送的 IQ 格式固定为 cs8")
        if sync_word is not None:
            sync_word = bytes(sync_word)
            if len(sync_word) != 4:
                raise ValueError("同步字必须为 4B")
        self.packet_format = PacketFormat(header_type, tuple(payload_lengths), crc_bytes, crc_initial)
        if header_type == "auto":
            raise ValueError("发送不能使用 auto 硬件头")
        self.config, self.sync_word, self.crc_initial = config, sync_word, self.packet_format.crc_initial
        self.depth = depth
        self._condition = threading.Condition()
        self._requests = deque()
        self._finished = self._aborted = False
        self._stats = {"packets_queued": 0, "bursts_generated": 0, "queue_max": 0,
                       "idle_samples": 0, "signal_samples": 0, "total_samples": 0}
        self._source = IQIterableSource(self._chunks())

    def _encode(self, payload, sync):
        fmt = self.packet_format
        if fmt.header_type == "fixed" and fmt.payload_lengths and len(payload) not in fmt.payload_lengths:
            raise ValueError(f"实际载荷 {len(payload)}B 不符合固定包长 {fmt.payload_lengths}")
        return encode_packet(payload, sync, self.crc_initial, self.config.coding_rate,
                             header_type=fmt.header_type, crc_bytes=fmt.crc_bytes)

    def submit(self, payload, *, sync_word=None, repeat=None, timeout=None):
        """校验后入队；满队列会阻塞或超时，finish/abort 会唤醒等待者。"""
        repeat = self.config.repeat if repeat is None else repeat
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
            raise ValueError("每个报文的 repeat 必须为正整数")
        if timeout is not None and (not isfinite(timeout) or timeout < 0):
            raise ValueError("报文入队 timeout 必须为有限非负数或 None")
        sync = self.sync_word if sync_word is None else sync_word
        if sync is None:
            raise ValueError("请指定默认同步字或报文自己的同步字")
        packet = self._encode(payload, sync)
        self._enqueue(_Request(packet, repeat), timeout)

    def submit_message(self, message, encoder, *, sync_word=None, repeat=None, timeout=None):
        """模板入队，每次重复在 IQ 生产线程重新编码；插件必须是纯函数。"""
        repeat = self.config.repeat if repeat is None else repeat
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
            raise ValueError("每个报文的 repeat 必须为正整数")
        if timeout is not None and (not isfinite(timeout) or timeout < 0):
            raise ValueError("报文入队 timeout 必须为有限非负数或 None")
        sync = self.sync_word if sync_word is None else bytes(sync_word)
        if sync is None or len(sync) != 4:
            raise ValueError("请指定 4B 同步字")
        template = MessageTemplate(encoder, message)
        # 启动前检查首帧，错误配置不能留到 USB 发送之后才发现。
        preview = template.encode(EncodeContext(0, round(self.config.guard_seconds * self.config.sample_rate)
                                               + self.config.ramp_samples, self.config.sample_rate))
        self._encode(preview, sync)
        self._enqueue(_Request(None, repeat, template, sync), timeout)
        return preview

    def _enqueue(self, request, timeout):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                if self._aborted:
                    raise StreamCancelled("发送源已停止")
                if self._finished:
                    raise ValueError("发送队列已结束，不能追加报文")
                if len(self._requests) < self.depth:
                    self._requests.append(request)
                    self._stats["packets_queued"] += 1
                    self._stats["queue_max"] = max(self._stats["queue_max"], len(self._requests))
                    self._condition.notify_all()
                    return
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("实时发送报文队列已满")
                self._condition.wait(remaining)

    def finish(self):
        """拒绝新报文；已排队的报文及重复次数全部产生后返回 EOF。"""
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def abort(self):
        """立即中止生成并唤醒所有阻塞的提交者；不承诺排空队列。"""
        with self._condition:
            self._aborted = True
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            return {**self._stats, "queue_remaining": len(self._requests)}

    def _record(self, size, kind=None):
        with self._condition:
            self._stats["total_samples"] += size
            if kind is not None:
                self._stats[kind] += size

    def _chunks(self):
        config = self.config
        guard = round(config.guard_seconds * config.sample_rate)
        gap = round(config.gap_seconds * config.sample_rate)
        idle_size = max(1, min(65536, round(0.005 * config.sample_rate)))
        position = 0
        frame_index = 0
        last_bits = last_waveform = None
        for chunk in _silence(guard):
            position += len(chunk)
            self._record(len(chunk))
            yield chunk
        while True:
            with self._condition:
                if self._aborted:
                    return
                request = self._requests.popleft() if self._requests else None
                finished = self._finished
                self._condition.notify_all()
            if request is None:
                if finished:
                    break
                # 不等上层输入：连续 USB 流的空闲时间用零 IQ 表示。
                self._record(idle_size, "idle_samples")
                position += idle_size
                yield np.zeros(idle_size, dtype=np.complex64)
                continue
            for _ in range(request.repeat):
                with self._condition:
                    if self._aborted:
                        return
                packet = request.packet
                if request.template is not None:
                    context = EncodeContext(frame_index, position + config.ramp_samples, config.sample_rate)
                    payload = request.template.encode(context)
                    packet = self._encode(payload, request.sync_word)
                bits = packet.bits.tobytes()
                if bits != last_bits:
                    last_waveform = modulate_bits(packet.bits, config)
                    last_bits = bits
                # 连续样本位置维持数字载波相位；每个物理突发仍保留各自的 RampTime。
                phase = 2 * pi * config.offset_hz * (position + np.arange(len(last_waveform))) / config.sample_rate
                waveform = (last_waveform * np.exp(1j * phase)).astype(np.complex64)
                self._record(len(waveform), "signal_samples")
                with self._condition:
                    self._stats["bursts_generated"] += 1
                position += len(waveform)
                frame_index += 1
                yield waveform
                for chunk in _silence(gap):
                    with self._condition:
                        if self._aborted:
                            return
                    position += len(chunk)
                    self._record(len(chunk))
                    yield chunk
        for chunk in _silence(guard):
            self._record(len(chunk))
            yield chunk

    def readinto(self, destination):
        return self._source.readinto(destination)
