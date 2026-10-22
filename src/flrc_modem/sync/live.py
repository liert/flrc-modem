"""USRP N310 与 HackRF 实时硬件时间戳 FHSS + TDD 同步会话。

无需 IQ 文件落盘，在流式接收的同时实时提取硬件时间戳、解调 FLRC 物理帧、
驱动同步状态机并支持 UHD Timed Commands 硬件跳频调度。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from math import isfinite
from pathlib import Path
import queue
import threading
import time
from typing import Iterator, Sequence

from ..config import DemodConfig
from ..io.iq import decode_samples
from ..models import DecodedFrame
from ..pipeline import DecodeStats, WindowDecoder
from ..realtime.windows import WindowQueue
from ..sdr.usrp import USRPConfig, USRPDevice
from .state_machine import SyncSnapshot, SyncState, SyncStateMachine
from .tdd import TDDMode
from .timestamp import HWTimestampExtractor
from .usrp_scheduler import LateTuneError, USRPTimedScheduler


DEFAULT_DK32S_HOPPING_TABLE = [
    2488460000, 2448460000, 2418060000, 2474860000, 2411660000,
    2420860000, 2445660000, 2431660000, 2460860000, 2500060000,
    2454860000, 2484060000, 2408860000, 2402460000, 2438060000,
    2427260000, 2464060000, 2471660000, 2477660000, 2440860000,
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
    late_tune_skips: int = 0
    reacquisition_count: int = 0
    clock_drift_ppm: float = 0.0
    turnaround_guard_us: float = 810.0
    current_slot: int = 0
    state: str = "searching"
    confidence: float = 0.0
    elapsed_seconds: float = 0.0
    error: str | None = None


def parse_rf_config(data: dict) -> tuple[list[int] | None, str | None]:
    """从射频配置字典中解析跳频频点列表与物理差分同步字 (OTA hex)。"""
    if not isinstance(data, dict):
        raise ValueError("射频配置顶层必须为 JSON 对象")
    # 1. 解析跳频表
    raw_table = data.get("hopping_table")
    if raw_table is None and "radio_config" in data and isinstance(data["radio_config"], dict):
        raw_table = data["radio_config"].get("hopping_table")
    hopping_table = None
    if raw_table is not None:
        if not isinstance(raw_table, list) or not raw_table:
            raise ValueError("hopping_table 必须为非空有序数组")
        hopping_table = []
        for index, item in enumerate(raw_table):
            value = item.get("frequency_hz") if isinstance(item, dict) else item
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0 or int(value) != value:
                raise ValueError("跳频表频率必须为正整数 Hz")
            if isinstance(item, dict) and item.get('slot_index', index) != index:
                raise ValueError("跳频表必须按 slot_index 顺序排列")
            hopping_table.append(int(value))

    # 2. 解析物理差分同步字
    sync_word = None
    radio_cfg = data.get("radio_config") if isinstance(data.get("radio_config"), dict) else {}
    sync_entry = data.get("sync_word") or radio_cfg.get("sync_word")
    if isinstance(sync_entry, dict):
        sync_word = sync_entry.get("ota_sync_word_hex") or sync_entry.get("raw_hex")
    elif isinstance(sync_entry, str):
        sync_word = sync_entry
    elif "ota_sync_word_hex" in data:
        sync_word = data["ota_sync_word_hex"]

    if sync_word and isinstance(sync_word, str):
        sync_word = sync_word.strip().lower()
        if sync_word.startswith("0x"):
            sync_word = sync_word[2:]
        try:
            if len(bytes.fromhex(sync_word)) != 4:
                raise ValueError()
        except ValueError:
            raise ValueError("射频同步字必须为 4B OTA 十六进制") from None
    elif sync_word is not None:
        raise ValueError("射频同步字必须为 4B OTA 十六进制")

    return hopping_table, sync_word


def resolve_rf_settings(rf_config=None, sync_word=None, frequency=None) -> dict:
    """共用的射频配置解析；显式指定的文件必须有效，不能静默回退历史跳频表。"""
    table = list(DEFAULT_DK32S_HOPPING_TABLE)
    source = "builtin_default"
    config_sync = None
    if rf_config is not None:
        from ..templates import resolve_template_path
        path = resolve_template_path(rf_config)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("顶层必须为 JSON 对象")
            table, config_sync = parse_rf_config(data)
            if not table:
                raise ValueError("缺少非空 hopping_table（按时隙顺序排列）")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError(f"射频配置 {path} 无效：{error}") from error
        source = str(path)
    sync = sync_word or config_sync
    if sync is not None:
        try:
            sync = sync.strip().lower().removeprefix("0x")
            if len(bytes.fromhex(sync)) != 4:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("射频同步字必须为 4B OTA 十六进制") from None
    initial = frequency if frequency is not None else table[0] if rf_config is not None else 2471660000
    if isinstance(initial, bool) or not isinstance(initial, (int, float)) or not isfinite(initial) or initial <= 0 or int(initial) != initial:
        raise ValueError("初始监听频率必须为正整数 Hz")
    warnings = []
    if rf_config is None:
        warnings.append("未提供当前设备的射频配置，正在使用内置历史跳频表；同步字可从空口恢复，但完整跳频顺序不能由单个频率或码率推算。")
    if initial not in table:
        warnings.append("初始监听频率不在所选跳频表中，请确认这是当前设备实际使用的观测频点。")
    return {"source": source, "hopping_table": table, "sync": sync,
            "frequency": int(initial), "warnings": warnings}


class LiveSyncSession:
    """实时流式同步分析会话。"""

    def __init__(
        self,
        *,
        sdr_type: str = "usrp",
        usrp_args: str = "addr=192.168.10.2",
        serial: str | None = None,
        device_index: int | None = None,
        frequency_hz: int = 2471660000,
        sample_rate: float | None = None,
        master_clock_rate: float | None = None,
        gain: float = 30.0,
        channel: int = 0,
        antenna: str = "RX2",
        sync_word: str | None = None,
        sync_words: Sequence[str] | None = None,
        hopping_table: Sequence[int] | None = None,
        tdd_mode: TDDMode = TDDMode.RECEPTION_TRIGGERED,
        timed_hopping: bool = False,
        duration: float = 0.0,
        window_queue_depth: int = 128,
        usrp_device: USRPDevice | None = None,
        protocol: str | None = "dk32s",
        observation_frequencies: Sequence[int] | None = None,
        bit_rate: float = 325000,
        coding_rate: str = "3/4",
        bt: float = 1.0,
    ):
        self.sdr_type = sdr_type.lower()
        self.usrp_args = usrp_args
        self.serial, self.device_index = serial, device_index
        self.frequency_hz = int(frequency_hz)
        self.observation_frequencies = set(observation_frequencies or (self.frequency_hz,))
        self.master_clock_rate = float(master_clock_rate) if master_clock_rate is not None else None

        # 智能对齐默认采样率：
        # 对于 USRP (尤其是 N310)，默认 MCR=125MHz 下 2.5MSps 为整除偶数分频(50分频)；HackRF 则默认 2MSps
        if sample_rate is not None:
            self.sample_rate = float(sample_rate)
        elif self.sdr_type == "usrp":
            if self.master_clock_rate == 122_880_000.0:
                self.sample_rate = 1_920_000.0   # 64分频
            elif self.master_clock_rate == 153_600_000.0:
                self.sample_rate = 2_400_000.0   # 64分频
            else:
                self.sample_rate = 2_500_000.0   # 默认 125MHz 下 50分频
        else:
            self.sample_rate = 2_000_000.0
        self.gain = float(gain)
        self.channel = int(channel)
        self.antenna = antenna
        self.sync_word = sync_word.lower() if sync_word else None
        # 收端可同时接受多个已知同步字（例如原字与极性反转后的回复字）。
        self.sync_words = tuple(str(word).lower() for word in (sync_words or ()))
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
            sync_words=tuple(bytes.fromhex(word) for word in self.sync_words),
            iq_format="cs8",
            chunk_seconds=0.010,
            bit_rate=bit_rate,
            coding_rate=coding_rate,
            bt=bt,
        )
        self.window_queue = WindowQueue(self.demod_config, depth=window_queue_depth)
        self._queue_depth = window_queue_depth
        self._configured = threading.Event()
        self.state_lock = threading.RLock()

        # 同步模块
        self.extractor = HWTimestampExtractor(sample_rate=self.sample_rate)
        self.state_machine = SyncStateMachine(
            self.hopping_table,
            sync_word=sync_word,
            sync_words=self.sync_words,
            tdd_mode=tdd_mode,
            protocol=protocol,
            bit_rate=bit_rate,
            coding_rate=coding_rate,
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
        self._schedule_thread: threading.Thread | None = None
        self._schedule_stop = threading.Event()
        self._tunes: list[tuple[float, int]] = [(float("-inf"), self.frequency_hz)]
        self._tune_lock = threading.RLock()
        self._tx_intervals = []
        self._last_hop_hw = float('-inf')
        self._hop_generation: int | None = None
        self._reacquiring = False
        self._reacquire_until_hw = float('-inf')

    def exclude_transmission(self, start: float, end: float, payload: bytes):
        with self.state_lock:
            self._tx_intervals = [(a, b, p) for a, b, p in self._tx_intervals if b > start - 1.0]
            self._tx_intervals.append((start, end, payload))

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
            target=self._dsp_worker,
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
        self._schedule_stop.set()
        self.window_queue.abort.set()

        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        if self._dsp_thread and self._dsp_thread.is_alive():
            self._dsp_thread.join(timeout=2.0)
        if self._schedule_thread and self._schedule_thread.is_alive():
            self._schedule_thread.join(timeout=2.0)

        self.stats.status = "failed" if self.stats.error else "stopped"
        self.stats.elapsed_seconds = time.monotonic() - self._start_time

    def _metadata_callback(self, has_time: bool, full_secs: int, frac_secs: float, sample_count: int, error_code: int):
        self.extractor.on_metadata(has_time, full_secs, frac_secs, sample_count, error_code)
        self.stats.overflow_count = self.extractor.overflow_count

    def _dsp_worker(self):
        try:
            self._dsp_loop()
        except Exception as error:
            self.stats.error = f"DSP: {type(error).__name__}: {error}"
            self.cancel_event.set()
            self.window_queue.abort.set()

    def _on_rate_configured(self, actual_rate: float):
        """当硬件实际生效采样率与预期不一致时（如 N310 硬件强制对齐），自适应重置解调与时间戳时钟。"""
        self.sample_rate = float(actual_rate)
        self.demod_config = replace(self.demod_config, sample_rate=self.sample_rate)
        self.window_queue = WindowQueue(self.demod_config, depth=self._queue_depth)
        self.extractor.sample_rate = self.sample_rate
        self.stats.sample_rate = self.sample_rate
        self._configured.set()

    def receive_frequency_at(self, hw_time: float) -> int:
        with self._tune_lock:
            return next(freq for when, freq in reversed(self._tunes) if when <= hw_time)

    @property
    def clock_ready_for_transmit(self) -> bool:
        """配合同步状态检查，阻止恢复调谐期间或旧跳频时钟上的发送。"""
        return (not self.timed_hopping or (not self._reacquiring
                and (self._hop_generation is None or self._hop_generation == self.state_machine.fhss.generation)))

    def _schedule_step(self):
        """失锁后有序返回观测信道，获得新锚点后才恢复跳频。"""
        scheduler = self._scheduler
        now = scheduler.device.get_time_estimate()
        with self.state_lock:
            sm = self.state_machine
            snap = sm.predict(now + scheduler.command_lead_time_s + 0.050)
            self.last_snapshot = snap
            generation = sm.fhss.generation
            locked = snap.state in (SyncState.LOCKED, SyncState.HOLDOVER)
            if self._reacquiring:
                if now < self._reacquire_until_hw or not locked:
                    return
                self._reacquiring = False
            recover = self._hop_generation is not None and (not locked or generation != self._hop_generation)

        if recover:
            slot = self.hopping_table.index(self.frequency_hz) if self.frequency_hz in self.hopping_table else 0
            parked = scheduler.schedule_rx_reacquisition(self.frequency_hz, slot, channel=self.channel)
            with self.state_lock:
                self._reacquire_until_hw = max(parked.scheduled_hw_time + scheduler.settling_s,
                                               sm.accept_anchors_from_hw)
                sm.reset_clock(sm.loss_reason or '跳频时钟已失效，返回观测信道重新捕获',
                               not_before_hw=self._reacquire_until_hw)
                self.last_snapshot = sm.predict(now)
                self._reacquiring = True
                self._hop_generation = None
                self._last_hop_hw = parked.scheduled_hw_time
                self.stats.reacquisition_count += 1
            with self._tune_lock:
                self._tunes.append((parked.scheduled_hw_time, self.frequency_hz))
            return

        if locked and snap.next_hop_hw_time > self._last_hop_hw + 1e-6:
            scheduler.schedule_rx_hop(snap.next_frequency_hz, snap.next_hop_hw_time,
                                      snap.next_slot_index, channel=self.channel)
            with self._tune_lock:
                self._tunes.append((snap.next_hop_hw_time, snap.next_frequency_hz))
            self._last_hop_hw = snap.next_hop_hw_time
            self._hop_generation = generation

    def _schedule_loop(self):
        """Advance with device time, independently of decoded packet arrivals."""
        while not self.cancel_event.is_set() and not self._schedule_stop.is_set():
            try:
                self._schedule_step()
            except LateTuneError:
                self.stats.late_tune_skips += 1
            except Exception as error:
                self.stats.error = f"Timed hopping: {type(error).__name__}: {error}"
                self.cancel_event.set()
                break
            self._schedule_stop.wait(0.005)

    def _rx_loop(self):
        """SDR IQ 采样接收主循环。"""
        try:
            if self.sdr_type == "usrp":
                dev = self._custom_usrp_dev or USRPDevice(device_args=self.usrp_args, master_clock_rate=self.master_clock_rate)
                close_after = self._custom_usrp_dev is None

                with dev if close_after else _dummy_context():
                    if self.timed_hopping:
                        self._scheduler = USRPTimedScheduler(dev, digital_only=True, settling_s=0.0005)

                    cfg = USRPConfig(
                        frequency_hz=self.frequency_hz,
                        sample_rate=self.sample_rate,
                        gain=self.gain,
                        channel=self.channel,
                        antenna=self.antenna,
                        device_args=self.usrp_args,
                        master_clock_rate=self.master_clock_rate,
                        cpu_format="cs8",
                        lo_frequency_hz=2450060000 if self.timed_hopping else None,
                        bandwidth_hz=100000000 if self.timed_hopping else None,
                    )

                    sample_limit = round(self.duration * self.sample_rate) if self.duration > 0 else 0

                    def sink_callback(chunk: bytes):
                        if self.cancel_event.is_set():
                            return
                        self.window_queue.write(chunk)
                        self.stats.total_bytes += len(chunk)
                        self.stats.total_samples += len(chunk) // 2

                    def configured(rate):
                        self._on_rate_configured(rate)
                        if self._scheduler:
                            self._schedule_thread = threading.Thread(target=self._schedule_loop,
                                                                     name="flrc-hop-scheduler", daemon=True)
                            self._schedule_thread.start()

                    try:
                        dev.receive(
                            sink_callback, cfg, sample_count=sample_limit,
                            cancel_event=self.cancel_event,
                            metadata_callback=self._metadata_callback,
                            rate_callback=configured, duration_s=self.duration,
                        )
                    finally:
                        self._schedule_stop.set()
                        if self._schedule_thread:
                            self._schedule_thread.join(timeout=2.0)
            else:
                # 兼容 HackRF 接收
                from ..sdr.hackrf import HackRFConfig, HackRFDevice, HackRFLibrary
                hackrf_cfg = HackRFConfig(
                    self.frequency_hz,
                    sample_rate=self.sample_rate,
                )
                with HackRFDevice(HackRFLibrary(), self.serial, device_index=self.device_index) as dev:
                    from ..sdr.hackrf.selection_cli import report_selected_device
                    report_selected_device(dev.selected_device)
                    self._on_rate_configured(self.sample_rate)
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
            try:
                self.window_queue.finish()
            finally:
                self.done_event.set()

    def _dsp_loop(self):
        """DSP 窗口解码与同步状态机更新循环。"""
        while not self._configured.wait(0.05):
            if self.done_event.is_set() or self.cancel_event.is_set():
                return
        decoder = WindowDecoder(self.demod_config, DecodeStats())
        generation = None

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
            if self.sdr_type == "usrp":
                timing = self.extractor.window_time(win.start_sample, win.sample_count)
                if timing is None:
                    continue
                win_time_s, epoch = timing
                if generation is not None and epoch != generation:
                    with self.state_lock:
                        # Absolute UHD timestamps recover the missing duration.
                        # Discard crossing windows, but retain the radio clock fit.
                        self.state_machine.overflow_count = self.extractor.overflow_count
                generation = epoch
            else:
                win_time_s = win.start_sample / self.sample_rate

            # A retune changes the physical centre even though packets stay at
            # baseband. Split windows at the recorded command times and discard
            # the configured settling interval instead of mislabelling frames.
            end_s = win_time_s + len(iq) / self.sample_rate
            with self._tune_lock:
                while len(self._tunes) > 2 and self._tunes[1][0] < win_time_s:
                    self._tunes.pop(0)
                tunes = list(self._tunes)
            cuts = [win_time_s] + [t for t, _ in tunes if win_time_s < t < end_s] + [end_s]
            frames = []
            for a, b in zip(cuts, cuts[1:]):
                tune_time, frequency = next((t, f) for t, f in reversed(tunes) if t <= a)
                if self._scheduler and tune_time != float("-inf"):
                    a = max(a, tune_time + self._scheduler.settling_s)
                if b <= a:
                    continue
                # Tracking only needs sparse clock anchors. Decode the original
                # observation channel once per cycle, plus requested replies;
                # following all hops must not force this Python decoder to
                # decode every packet in order to keep hardware scheduling alive.
                if self.timed_hopping and frequency not in self.observation_frequencies:
                    with self.state_lock:
                        interested = any(x <= b and y + 0.006 >= a for x, y, _ in self._tx_intervals)
                    if not interested:
                        continue
                if decoder.config.center_frequency != frequency:
                    cfg = replace(self.demod_config, center_frequency=frequency, frequencies=(frequency,))
                    recent = decoder.recent
                    decoder = WindowDecoder(cfg, decoder.stats)
                    decoder.recent = recent
                lo = max(0, round((a - win_time_s) * self.sample_rate))
                hi = min(len(iq), round((b - win_time_s) * self.sample_rate))
                if hi > lo:
                    frames.extend(decoder.decode(iq[lo:hi], win_time_s + lo / self.sample_rate))

            for frame in frames:
                # 实时灌入同步状态机
                with self.state_lock:
                    echo = any(a - 0.00025 <= frame.time_s <= b + 0.00025 and p == frame.packet.payload
                               for a, b, p in self._tx_intervals)
                    if not echo:
                        self.state_machine.on_frame(frame.time_s, frame)
                    snap = self.state_machine.predict(frame.time_s)

                self.last_snapshot = snap
                self.stats.decoded_frames += 1
                self.stats.state = snap.state.value
                self.stats.confidence = snap.confidence
                self.stats.clock_drift_ppm = snap.clock_drift_ppm
                self.stats.turnaround_guard_us = snap.turnaround_guard_s * 1e6
                self.stats.current_slot = snap.slot_index

                try:
                    self.frame_queue.put_nowait((frame, snap))
                except queue.Full:
                    pass

        # 结束标记
        try:
            self.frame_queue.put_nowait(None)
        except queue.Full:
            pass

    def events(self) -> Iterator[tuple[DecodedFrame, SyncSnapshot]]:
        """迭代产生解调出的物理帧及其伴随的实时同步预测快照。"""
        while True:
            try:
                item = self.frame_queue.get(timeout=0.1)
                if item is None:
                    break
                yield item
            except queue.Empty:
                if self.done_event.is_set() and (self._dsp_thread is None or not self._dsp_thread.is_alive()) and self.frame_queue.empty():
                    break


class _dummy_context:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
