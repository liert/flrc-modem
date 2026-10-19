"""采集控制在线程运行，DSP 默认在独立进程逐窗解调；无需 IQ 文件落盘。"""
from dataclasses import asdict, dataclass, field
from math import ceil, isfinite
import queue
import threading
import time

from ..config import DemodConfig
from ..io.iq import decode_samples
from ..pipeline import DecodeStats, WindowDecoder
from ..sdr.hackrf import (HackRFConfig, HackRFDevice, HackRFError, HackRFLibrary,
                         StreamCancelled, StreamOverrun)
from .windows import WindowQueue
from .worker import ProcessDecoder


@dataclass
class LiveStats:
    status: str = "created"
    sample_rate_hz: float = 0.0
    input_samples: int = 0
    discarded_bytes: int = 0
    windows_queued_max: int = 0
    queue_delay_seconds_max: float = 0.0
    dsp_seconds: float = 0.0
    dsp_compute_seconds: float = 0.0
    dsp_mode: str = "process"
    window_processing_seconds_max: float = 0.0
    elapsed_seconds: float = 0.0
    hardware_shortfalls: int = 0
    monitor_error: str | None = None
    decode: DecodeStats = field(default_factory=DecodeStats)
    capture: dict | None = None

    def to_dict(self):
        return asdict(self)


class LiveReceiver:
    def __init__(self, config: DemodConfig, hardware: HackRFConfig, *, duration=0.0,
                 dll_path=None, serial=None, window_queue_depth=8, rx_queue_depth=32,
                 block_bytes=None, health_interval=1.0, allow_device_shortfalls=False,
                 device_factory=None, dsp_process=True):
        if config.sample_rate != hardware.sample_rate or config.center_frequency != hardware.frequency_hz:
            raise ValueError("解调器与 HackRF 的采样率、中心频率必须一致")
        if config.iq_format != "cs8":
            raise ValueError("HackRF 实时输入固定为 cs8")
        if not isfinite(duration) or duration < 0:
            raise ValueError("duration 必须为有限非负数，0 表示持续接收")
        if not isfinite(health_interval) or health_interval <= 0:
            raise ValueError("health_interval 必须为有限正数")
        self.config, self.hardware, self.duration = config, hardware, duration
        self.windows = WindowQueue(config, window_queue_depth)
        self.stats = LiveStats()
        self.stats.sample_rate_hz = config.sample_rate
        self.stats.dsp_mode = "process" if dsp_process else "thread"
        self.decoder = WindowDecoder(config, self.stats.decode)
        self.dsp_process = dsp_process
        self._worker = None
        self.device_factory = device_factory or (lambda: HackRFDevice(HackRFLibrary(dll_path), serial))
        self.rx_options = {"queue_depth": rx_queue_depth, "block_bytes": block_bytes,
                           "allow_device_shortfalls": allow_device_shortfalls,
                           "health_interval": health_interval}
        self.allow_device_shortfalls = allow_device_shortfalls
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.error = None
        self._thread = None
        self._began = 0.0
        self._frames_started = False

    def __enter__(self):
        if self._thread is not None:
            raise RuntimeError("实时会话不能重复启动")
        # 初始化完成后再打开设备，避免启动 Python/SciPy 时积累采样。
        if self.dsp_process:
            self._worker = ProcessDecoder(self.config)
        self._began = time.monotonic()
        self.stats.status = "running"
        self._thread = threading.Thread(target=self._capture, name="flrc-live-capture", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            if self._worker is not None:
                self._worker.close()
            raise
        return self

    def _capture(self):
        device = None
        monitor_available = True
        def monitor():
            nonlocal monitor_available
            if not monitor_available:
                return
            try:
                state = device.state()
            except AttributeError as error:
                monitor_available = False
                self.stats.monitor_error = str(error)
                return
            except HackRFError as error:
                if "-1005" not in str(error):
                    raise
                monitor_available = False
                self.stats.monitor_error = str(error)
                return
            self.stats.hardware_shortfalls = state["num_shortfalls"]
            if state["error"]:
                raise HackRFError(f"实时接收固件错误：{state['error']}")
            if state["num_shortfalls"] and not self.allow_device_shortfalls:
                raise StreamOverrun(f"实时接收固件溢出 {state['num_shortfalls']} 次，停止以避免静默丢样")
        try:
            device = self.device_factory()
            with device:
                try:
                    device.receive(self.windows, self.hardware,
                                   sample_count=ceil(self.duration * self.config.sample_rate) if self.duration else None,
                                   timeout=self.duration + 10 if self.duration else None,
                                   cancel_event=self.cancel, health_check=monitor, **self.rx_options)
                    self.stats.status = "completed"
                except StreamCancelled:
                    if not self.cancel.is_set():
                        raise
                    self.stats.status = "cancelled"
            # native RX 和消费者线程均停止后才补交残余窗口。
            if not self.windows.abort.is_set():
                self.windows.finish()
        except BaseException as error:
            self.error = error
            self.stats.status = "failed"
        finally:
            if device is not None and device.last_stats is not None:
                self.stats.capture = device.last_stats.to_dict()
                state = device.last_stats.device_state
                if state is not None:
                    self.stats.hardware_shortfalls = state["num_shortfalls"]
            self.done.set()

    def frames(self):
        if self._thread is None:
            raise RuntimeError("请在 with LiveReceiver(...) 中读取帧")
        if self._frames_started:
            raise RuntimeError("每个实时会话只能创建一个帧消费者")
        self._frames_started = True
        while True:
            try:
                window = self.windows.ready.get(timeout=0.05)
            except queue.Empty:
                if self.done.is_set():
                    break
                continue
            try:
                self.stats.queue_delay_seconds_max = max(self.stats.queue_delay_seconds_max,
                                                          time.monotonic() - window.queued_at)
                begin = time.perf_counter()
                if self._worker is not None:
                    frames, counters, compute = self._worker.decode(bytes(self.windows.view(window)),
                                                                   window.start_sample / self.config.sample_rate)
                    for name, value in counters.items():
                        setattr(self.stats.decode, name, value)
                else:
                    iq = decode_samples(self.windows.view(window), "cs8")
                    frames = list(self.decoder.decode(iq, window.start_sample / self.config.sample_rate))
                    compute = time.perf_counter() - begin
                elapsed = time.perf_counter() - begin
                self.stats.dsp_seconds += elapsed
                self.stats.dsp_compute_seconds += compute
                self.stats.window_processing_seconds_max = max(self.stats.window_processing_seconds_max, elapsed)
            finally:
                self.windows.release(window)
            yield from frames
        if self.error is not None:
            raise self.error

    def stop(self):
        """请求停止采集；frames 消费者仍可读完已经排队的窗口。"""
        self.cancel.set()

    def __exit__(self, exception_type, exception, traceback):
        self.windows.abort.set()
        self.cancel.set()
        self._thread.join(timeout=15)
        if self._worker is not None:
            self._worker.close()
            self._worker = None
        self.stats.input_samples = self.windows.input_bytes // 2
        self.stats.discarded_bytes = self.windows.discarded_bytes
        self.stats.windows_queued_max = self.windows.high_watermark
        self.stats.elapsed_seconds = time.monotonic() - self._began
        if exception is not None:
            self.stats.status = "cancelled" if isinstance(exception, KeyboardInterrupt) else "failed"
        if self._thread.is_alive():
            error = "实时采集控制线程未能及时停止"
            if exception is None:
                raise TimeoutError(error)
            exception.add_note(error)
