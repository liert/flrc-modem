"""USRP N310 与 HackRF 实时硬件时间戳 FHSS + TDD 同步会话。

无需 IQ 文件落盘，在流式接收的同时实时提取硬件时间戳、解调 FLRC 物理帧、
驱动同步状态机并支持 UHD Timed Commands 硬件跳频调度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import queue
import sys
import threading
import time
from typing import Callable, Iterator, Sequence

from ..config import DemodConfig
from ..io.iq import decode_samples
from ..models import DecodedFrame
from ..pipeline import DecodeStats, WindowDecoder
from ..realtime.windows import RealtimeBacklogError, WindowQueue
from ..sdr.usrp import UHDLibrary, USRPConfig, USRPDevice
from .fhss import FHSSSynchronizer
from .state_machine import SyncSnapshot, SyncState, SyncStateMachine
from .tdd import TDDMode, TDDSynchronizer
from .timestamp import HWTimestamp, HWTimestampExtractor
from .usrp_scheduler import USRPTimedScheduler


DEFAULT_DK32S_HOPPING_TABLE = [
    2488460000, 2448460000, 2418060000, 2474860000, 2411660000,
    2420860000, 2445660000, 2431660000, 2460860000, 2426060000,
    2482860000, 2454060000, 2437260000, 2414860000, 2451260000,
    2485660000, 2464060000, 2471660000, 2477660000, 2440860000,
]


@dataclass
class LiveSyncStats:
    status: str = "created"
    sdr_type: str = "usrp"
    sample_rate: float = 2000000.0
    total_samples: int = 0
    total_bytes: int = 0
    decoded_frames: int = 0
    overflow_count: int = 0
    clock_drift_ppm: float = 0.0
    turnaround_guard_us: float = 810.0
    current_slot: int = 0
    state: str = "searching"
    confidence: float = 0.0
    elapsed_seconds: float = 0.0
    error: str | None = None


class LiveSyncSession:
    """实时流式同步分析会话。"""

    def __init__(
        self,
        *,
        sdr_type: str = "usrp",
        usrp_args: str = "addr=192.168.10.2",
        frequency_hz: int = 2471660000,
        sample_rate: float = 2000000.0,
        gain: float = 30.0,
        channel: int = 0,
        sync_word: str = "ecee6698",
        hopping_table: Sequence[int] | None = None,
        tdd_mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        timed_hopping: bool = False,
        duration: float = 0.0,
        window_queue_depth: int = 16,
        usrp_device: USRPDevice | None = None,
    ):
        self.sdr_type = sdr_type.lower()
        self.usrp_args = usrp_args
        self.frequency_hz = int(frequency_hz)
        self.sample_rate = float(sample_rate)
        self.gain = float(gain)
        self.channel = int(channel)
        self.sync_word = sync_word
        self.hopping_table = list(hopping_table or DEFAULT_DK32S_HOPPING_TABLE)
        self.tdd_mode = tdd_mode
        self.timed_hopping = timed_hopping
        self.duration = float(duration)

        # 核心 DSP 配置
        self.demod_config = DemodConfig(
            sample_rate=self.sample_rate,
            center_frequency=self.frequency_hz,
            frequencies=(self.frequency_hz,),
            sync_word=bytes.fromhex(sync_word) if sync_word else None,
            iq_format="cs8",
        )
        self.window_queue = WindowQueue(self.demod_config, depth=window_queue_depth)

        # 同步模块
        self.extractor = HWTimestampExtractor(sample_rate=self.sample_rate)
        self.state_machine = SyncStateMachine(
            self.hopping_table,
            sync_word=sync_word,
            tdd_mode=tdd_mode,
        )
        self.stats = LiveSyncStats(
            sdr_type=self.sdr_type,
            sample_rate=self.sample_rate,
        )

        # 硬件与线程控制
        self.cancel_event = threading.Event()
        self.done_event = threading.Event()
        self.frame_queue: queue.Queue[tuple[DecodedFrame, SyncSnapshot] | None] = queue.Queue(maxsize=128)

        self._rx_thread: threading.Thread | None = None
        self._dsp_thread: threading.Thread | None = None
        self._start_time: float = 0.0
        self.last_snapshot: SyncSnapshot | None = None

        self._custom_usrp_dev = usrp_device
        self._scheduler: USRPTimedScheduler | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """启动流式接收线程与 DSP 解调/同步线程。"""
        if self._rx_thread is not None:
            raise RuntimeError("LiveSyncSession 已在运行")

        self.stats.status = "running"
        self._start_time = time.monotonic()

        self._dsp_thread = threading.Thread(
            target=self._dsp_loop,
            name="flrc-sync-dsp",
            daemon=True,
        )
        self._dsp_thread.start()

        self._rx_thread = threading.Thread(
            target=self._rx_loop,
            name="flrc-sync-rx",
            daemon=True,
        )
        self._rx_thread.start()

    def stop(self):
        """停止实时会话并清理资源。"""
        self.cancel_event.set()
        self.window_queue.abort.set()

        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        if self._dsp_thread and self._dsp_thread.is_alive():
            self._dsp_thread.join(timeout=2.0)

        self.stats.status = "stopped"
        self.stats.elapsed_seconds = time.monotonic() - self._start_time

    def _metadata_callback(self, has_time: bool, full_secs: int, frac_secs: float, sample_count: int, error_code: int):
        ts = self.extractor.on_metadata(has_time, full_secs, frac_secs, sample_count, error_code)
        if ts is None and self.extractor.discontinuity_flag:
            self.state_machine.on_discontinuity("UHD 硬件溢出")
            self.stats.overflow_count = self.extractor.overflow_count

    def _rx_loop(self):
        """SDR IQ 采样接收主循环。"""
        try:
            if self.sdr_type == "usrp":
                dev = self._custom_usrp_dev or USRPDevice(device_args=self.usrp_args)
                close_after = self._custom_usrp_dev is None

                with dev if close_after else _dummy_context():
                    if self.timed_hopping:
                        self._scheduler = USRPTimedScheduler(dev)

                    cfg = USRPConfig(
                        frequency_hz=self.frequency_hz,
                        sample_rate=self.sample_rate,
                        gain=self.gain,
                        channel=self.channel,
                        device_args=self.usrp_args,
                        cpu_format="cs8",
                    )

                    sample_limit = round(self.duration * self.sample_rate) if self.duration > 0 else 0

                    def sink_callback(chunk: bytes):
                        if self.cancel_event.is_set():
                            return
                        self.window_queue.write(chunk)
                        self.stats.total_bytes += len(chunk)
                        self.stats.total_samples += len(chunk) // 2

                    dev.receive(
                        sink_callback,
                        cfg,
                        sample_count=sample_limit,
                        cancel_event=self.cancel_event,
                        metadata_callback=self._metadata_callback,
                    )
            else:
                # 兼容 HackRF 接收
                from ..sdr.hackrf import HackRFConfig, HackRFDevice, HackRFLibrary
                hackrf_cfg = HackRFConfig(
                    self.frequency_hz,
                    sample_rate=self.sample_rate,
                )
                with HackRFDevice(HackRFLibrary()) as dev:
                    def hackrf_sink(chunk: bytes):
                        if self.cancel_event.is_set():
                            return
                        self.window_queue.write(chunk)
                        self.stats.total_bytes += len(chunk)
                        self.stats.total_samples += len(chunk) // 2

                    dev.receive(
                        hackrf_sink,
                        hackrf_cfg,
                        duration=self.duration,
                        cancel_event=self.cancel_event,
                    )
        except Exception as e:
            self.stats.error = f"{type(e).__name__}: {e}"
        finally:
            self.window_queue.finish()
            self.done_event.set()

    def _dsp_loop(self):
        """DSP 窗口解码与同步状态机更新循环。"""
        decoder = WindowDecoder(self.demod_config, DecodeStats())
        base_hw_s = 0.0

        while not self.cancel_event.is_set() or not self.window_queue.ready.empty():
            try:
                win = self.window_queue.ready.get(timeout=0.05)
            except queue.Empty:
                if self.done_event.is_set():
                    break
                continue

            # 从 WindowQueue 视图获取并解调采样
            try:
                buf = self.window_queue.buffers[win.slot][:win.sample_count * 2]
                iq = decode_samples(buf, "cs8")
            finally:
                self.window_queue.release(win)

            # 计算窗口首采样的硬件时间戳
            if self.extractor.last_hw_anchor is not None:
                win_time_s = self.extractor.last_hw_anchor.time_s + (win.start_sample / self.sample_rate)
            else:
                win_time_s = win.start_sample / self.sample_rate

            frames = list(decoder.decode(iq, win_time_s))

            for frame in frames:
                # 实时灌入同步状态机
                accepted = self.state_machine.on_frame(frame.time_s, frame)
                snap = self.state_machine.predict(frame.time_s)

                self.last_snapshot = snap
                self.stats.decoded_frames += 1
                self.stats.state = snap.state.value
                self.stats.confidence = snap.confidence
                self.stats.clock_drift_ppm = snap.clock_drift_ppm
                self.stats.turnaround_guard_us = snap.turnaround_guard_s * 1e6
                self.stats.current_slot = snap.slot_index

                # 如果启用了 USRP 硬件定时跳频调度且进入锁定态
                if self.timed_hopping and self._scheduler and snap.state == SyncState.LOCKED:
                    # 安排下一跳时刻提前执行
                    if snap.slot_remaining_s > 0.010:
                        try:
                            self._scheduler.schedule_rx_hop(
                                target_frequency_hz=snap.next_frequency_hz,
                                hop_hw_time=snap.next_hop_hw_time,
                                slot_index=snap.next_slot_index,
                                channel=self.channel,
                            )
                        except Exception:
                            pass

                try:
                    self.frame_queue.put_nowait((frame, snap))
                except queue.Full:
                    pass

        # 结束标记
        self.frame_queue.put(None)

    def events(self) -> Iterator[tuple[DecodedFrame, SyncSnapshot]]:
        """迭代产生解调出的物理帧及其伴随的实时同步预测快照。"""
        while True:
            try:
                item = self.frame_queue.get(timeout=0.1)
                if item is None:
                    break
                yield item
            except queue.Empty:
                if self.done_event.is_set() and self.frame_queue.empty():
                    break


class _dummy_context:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
