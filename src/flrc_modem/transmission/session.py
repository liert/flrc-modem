"""报文级实时发送会话，复用底层固定池、TX flush 与设备清理。"""
from dataclasses import asdict, dataclass
from math import isfinite
import threading
import time

from ..modulation import ModulationConfig
from ..sdr.hackrf import (HackRFConfig, HackRFDevice, HackRFError, HackRFLibrary,
                         StreamCancelled, StreamUnderrun)
from .source import PacketSource


@dataclass
class TransmitStats:
    status: str = "created"
    device: dict | None = None
    sample_rate_hz: float = 0
    elapsed_seconds: float = 0
    hardware_shortfalls: int = 0
    monitor_error: str | None = None
    source: dict | None = None
    stream: dict | None = None

    def to_dict(self):
        return asdict(self)


class LiveTransmitter:
    def __init__(self, modulation: ModulationConfig, hardware: HackRFConfig, sync_word=None, *,
                 crc_initial=None, header_type="variable", crc_bytes=3, payload_lengths=(), duration=0, input_queue_depth=64, queue_depth=8,
                 prefill_blocks=4, block_bytes=None, timeout=None, health_interval=1,
                 underrun_policy="stop", allow_device_shortfalls=False,
                 dll_path=None, serial=None, device_index=None, on_device_open=None, device_factory=None):
        if modulation.sample_rate != hardware.sample_rate:
            raise ValueError("调制器与 HackRF 的采样率必须一致")
        if not isfinite(duration) or duration < 0:
            raise ValueError("duration 必须为有限非负数")
        if not isfinite(health_interval) or health_interval <= 0:
            raise ValueError("health_interval 必须为有限正数")
        self.source = PacketSource(modulation, sync_word, crc_initial, depth=input_queue_depth,
                                   header_type=header_type, crc_bytes=crc_bytes, payload_lengths=payload_lengths)
        self.stats = TransmitStats(sample_rate_hz=modulation.sample_rate)
        self.hardware, self.duration = hardware, duration
        self.device_factory = device_factory or (lambda: HackRFDevice(HackRFLibrary(dll_path), serial,
                                                                    device_index=device_index))
        self.on_device_open = on_device_open
        self.options = dict(queue_depth=queue_depth, prefill_blocks=prefill_blocks, block_bytes=block_bytes,
                            timeout=timeout, underrun_policy=underrun_policy, health_interval=health_interval,
                            allow_device_shortfalls=allow_device_shortfalls)
        self.allow_device_shortfalls = allow_device_shortfalls
        self.cancel = threading.Event()
        self.done = threading.Event()
        self._duration_reached = threading.Event()
        self._thread = None
        self.error = None

    def submit(self, payload, *, sync_word=None, repeat=None, timeout=None):
        """可在启动前预放首包，也可在运行期间由业务线程持续入队。"""
        self.source.submit(payload, sync_word=sync_word, repeat=repeat, timeout=timeout)

    def finish(self):
        self.source.finish()

    def submit_message(self, message, encoder, *, sync_word=None, repeat=None, timeout=None):
        """发送业务模板；每个突发分别更新字段并计算 sum8/CRC24。"""
        return self.source.submit_message(message, encoder, sync_word=sync_word, repeat=repeat, timeout=timeout)

    def stop(self):
        """立即停止，可能截断当前包；使用 finish + wait 可排空后正常结束。"""
        self.cancel.set()
        self.source.abort()

    def __enter__(self):
        if self._thread is not None:
            raise RuntimeError("实时发送会话不能重复启动")
        self.stats.status = "running"
        self._thread = threading.Thread(target=self._run, name="flrc-live-tx", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        device = timer = None
        begin = time.monotonic()
        monitor_available = True
        def limit_duration():
            self._duration_reached.set()
            self.stop()
        def monitor():
            nonlocal monitor_available
            if not monitor_available:
                return
            try:
                state = device.state()
            except (AttributeError, HackRFError) as error:
                if isinstance(error, HackRFError) and "-1005" not in str(error):
                    raise
                monitor_available = False
                self.stats.monitor_error = str(error)
                return
            self.stats.hardware_shortfalls = state["num_shortfalls"]
            if state["error"]:
                raise HackRFError(f"发送固件错误：{state['error']}")
            if state["num_shortfalls"] and not self.allow_device_shortfalls:
                raise StreamUnderrun(f"发送固件欠载 {state['num_shortfalls']} 次")
        try:
            device = self.device_factory()
            with device:
                self.stats.device = device.selected_device
                if self.on_device_open is not None:
                    self.on_device_open(self.stats.device)
                if self.duration:
                    timer = threading.Timer(self.duration, limit_duration)
                    timer.start()
                try:
                    device.transmit(self.source, self.hardware, cancel_event=self.cancel,
                                    health_check=monitor, **self.options)
                    # 取消可能恰好让生产源 EOF/flush；底层正常返回也不能误报为完整发送。
                    if self.cancel.is_set():
                        self.stats.status = "duration_reached" if self._duration_reached.is_set() else "cancelled"
                    else:
                        self.stats.status = "completed"
                except StreamCancelled:
                    if not self.cancel.is_set():
                        raise
                    self.stats.status = "duration_reached" if self._duration_reached.is_set() else "cancelled"
        except BaseException as error:
            self.error = error
            self.stats.status = "failed"
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            # 失败或取消时唤醒上层提交者，防止其永远堵在满队列中。
            self.source.abort()
            self.stats.source = self.source.snapshot()
            if device is not None and device.last_stats is not None:
                self.stats.stream = device.last_stats.to_dict()
                state = device.last_stats.device_state
                if state is not None:
                    self.stats.hardware_shortfalls = state["num_shortfalls"]
                    if self.error is None and (state["error"] or (
                            state["num_shortfalls"] and not self.allow_device_shortfalls)):
                        self.error = StreamUnderrun(f"发送停止后的固件状态异常：{state}")
                        self.stats.status = "failed"
            self.stats.elapsed_seconds = time.monotonic() - begin
            self.done.set()

    def wait(self):
        if self._thread is None:
            raise RuntimeError("请先进入 with LiveTransmitter(...) 启动发送")
        while not self.done.wait(0.05):
            pass
        self._thread.join()
        if self.error is not None:
            raise self.error
        return self.stats

    def __exit__(self, exception_type, exception, traceback):
        if exception is None:
            self.finish()
            try:
                self.wait()
            except BaseException:
                self.stop()
                self._thread.join(timeout=15)
                raise
        else:
            self.stop()
            self._thread.join(timeout=15)
            if not isinstance(exception, (KeyboardInterrupt, StreamCancelled)):
                self.stats.status = "failed"
        if self._thread.is_alive():
            error = "实时发送控制线程未能及时停止"
            if exception is None:
                raise TimeoutError(error)
            exception.add_note(error)
