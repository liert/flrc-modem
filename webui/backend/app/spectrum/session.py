"""SDR 实时频谱采集与处理会话：管理设备句柄、接收与 DSP 解耦线程、有界缓冲及状态度量。"""
from __future__ import annotations

import asyncio
import ctypes as C
import logging
from math import isfinite
import queue
import threading
import time
from typing import Any, Callable, Literal
import uuid

import numpy as np

from ..devices.manager import get_device_manager
from .dsp import compute_spectrum, convert_raw_iq_to_complex, generate_test_tone_iq
from .schemas import SpectrumStartRequest, SpectrumStatusResponse, SpectrumUpdateRequest

logger = logging.getLogger(__name__)


class _QueueSink:
    """供 HackRF RxStream.write 调用的适配器，将接收的原始字节安全投递入有界队列。"""

    def __init__(self, out_queue: queue.Queue, on_overflow: Callable[[], None]):
        self._queue = out_queue
        self._on_overflow = on_overflow

    def write(self, buffer: memoryview) -> int:
        data = bytes(buffer)
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            self._on_overflow()
        return len(data)


class SpectrumSession:
    """单个实时频谱采集会话。"""

    def __init__(self, request: SpectrumStartRequest):
        self.session_id = f"spec_{uuid.uuid4().hex[:8]}"
        self.request = request
        self.sdr_type = request.sdr.lower()
        self.state: Literal["idle", "running", "paused", "stopped", "error"] = "idle"

        # 硬件与实际运行参数
        self.actual_center_freq = request.center_freq
        self.actual_sample_rate = request.sample_rate
        self.actual_master_clock: float | None = request.master_clock_rate
        self.device_id: str | None = None
        self.device_name: str | None = None
        self.n_fft = request.n_fft
        self.window = request.window
        self.fft_average = request.fft_average
        self.fps_limit = request.fps_limit
        self.dynamic_range_db = request.dynamic_range_db
        self.ref_level_dbfs = request.ref_level_dbfs

        # 性能与丢帧度量
        self.hw_overflows = 0
        self.dsp_dropped_frames = 0
        self.client_dropped_frames = 0
        self.fps = 0.0
        self.error_code: str | None = None
        self.error_message: str | None = None

        # 控制与生命周期
        self._cancel_event = threading.Event()
        self._is_paused = False
        self._rx_thread: threading.Thread | None = None
        self._dsp_thread: threading.Thread | None = None
        self._device: Any = None
        self._selector: str | None = None

        # 有界解耦队列
        # rx_queue: 硬件接收 -> DSP 处理 (最大容纳 8 块数据，防内存泄漏)
        self._rx_queue: queue.Queue[bytes] = queue.Queue(maxsize=8)
        # ws_frame_queue: DSP 处理 -> WebSocket 客户端 (由 async loop 消费，最大深度 2)
        self._latest_frame: dict[str, Any] | None = None
        self._frame_subscribers: set[asyncio.Queue] = set()
        self._frame_lock = threading.Lock()

    def start(self):
        """申请设备独占锁，初始化硬件并启动解耦工作线程。"""
        device_mgr = get_device_manager()

        if self.sdr_type != "sim":
            params = self.request.model_dump()
            self._selector = device_mgr.resolve_selector(self.sdr_type, params)
            acquired, err = device_mgr.acquire_device(self.sdr_type, self.session_id, self._selector)
            if not acquired:
                self.state = "error"
                self.error_code = "DEVICE_BUSY"
                self.error_message = err or f"设备 '{self.sdr_type.upper()}' 已被其他任务独占锁定"
                raise RuntimeError(self.error_message)

        try:
            self._init_hardware()
            self.state = "running"
            self._cancel_event.clear()

            # 启动 DSP 处理工作线程
            self._dsp_thread = threading.Thread(
                target=self._dsp_worker,
                name=f"spectrum-dsp-{self.session_id}",
                daemon=True,
            )
            self._dsp_thread.start()

            # 启动硬件接收线程
            self._rx_thread = threading.Thread(
                target=self._rx_worker,
                name=f"spectrum-rx-{self.session_id}",
                daemon=True,
            )
            self._rx_thread.start()
            logger.info("实时频谱会话 %s 启动成功 (SDR=%s)", self.session_id, self.sdr_type)
        except Exception as err:
            self.state = "error"
            if not self.error_code:
                self.error_code = "DEVICE_OPEN_FAILED"
            self.error_message = str(err)
            self.cleanup()
            raise

    def _init_hardware(self):
        """初始化底层 USRP 或 HackRF 硬件句柄。"""
        if self.sdr_type == "hackrf":
            from flrc_modem.sdr.hackrf.device import HackRFDevice

            dev = HackRFDevice(
                serial=self.request.serial,
                device_index=self.request.device_index,
            )
            dev.__enter__()
            self._device = dev
            self.device_id = dev.serial or (f"index_{dev.device_index}" if dev.device_index is not None else "hackrf_0")
            self.device_name = "HackRF One"
            self.actual_center_freq = float(self.request.center_freq)
            self.actual_sample_rate = float(self.request.sample_rate)

        elif self.sdr_type == "usrp":
            from flrc_modem.sdr.usrp.device import USRPDevice

            dev = USRPDevice(
                device_args=self.request.args or "",
                master_clock_rate=self.request.master_clock_rate,
            )
            dev.__enter__()
            self._device = dev
            self.device_id = dev.device_args or "usrp_default"
            self.device_name = dev.device_info.get("mboard_name", "USRP")
            if dev.device_info.get("master_clock_rate"):
                self.actual_master_clock = float(dev.device_info["master_clock_rate"])
            elif hasattr(dev, "get_master_clock_rate"):
                try:
                    self.actual_master_clock = float(dev.get_master_clock_rate())
                except Exception:
                    pass

        elif self.sdr_type == "sim":
            self.device_id = "sim_0"
            self.device_name = "纯软件高保真仿真信号源"
            self.actual_center_freq = float(self.request.center_freq)
            self.actual_sample_rate = float(self.request.sample_rate)

        else:
            raise ValueError(f"未知 SDR 类型: {self.sdr_type}")

    def _on_rx_overflow(self):
        """处理数据溢出或 DSP 处理不及时丢弃。"""
        self.dsp_dropped_frames += 1

    def _rx_worker(self):
        """硬件接收工作线程：流式获取 IQ 块并压入有界队列。"""
        try:
            if self.sdr_type == "hackrf":
                from flrc_modem.sdr.hackrf.config import HackRFConfig

                config = HackRFConfig(
                    frequency_hz=int(self.actual_center_freq),
                    sample_rate=int(self.actual_sample_rate),
                    lna_gain=self.request.lna_gain or 16,
                    vga_gain=self.request.vga_gain or 20,
                    amp=self.request.amp,
                    bias_tee=self.request.bias_tee,
                )
                sink = _QueueSink(self._rx_queue, self._on_rx_overflow)
                self._device.receive(
                    sink,
                    config,
                    sample_count=None,
                    cancel_event=self._cancel_event,
                    block_bytes=262144,
                )

            elif self.sdr_type == "usrp":
                from flrc_modem.sdr.usrp.config import USRPConfig

                config = USRPConfig(
                    sample_rate=float(self.actual_sample_rate),
                    frequency_hz=float(self.actual_center_freq),
                    gain=float(self.request.gain or 30.0),
                    antenna=self.request.antenna or "TX/RX",
                    channel=self.request.channel,
                    master_clock_rate=self.request.master_clock_rate,
                    cpu_format="fc32",
                )
                actual_rate = self._device.configure_rx(config)
                self.actual_sample_rate = actual_rate

                def _usrp_sink(chunk_bytes: bytes):
                    try:
                        self._rx_queue.put_nowait(chunk_bytes)
                    except queue.Full:
                        self._on_rx_overflow()

                def _meta_cb(**kwargs):
                    if kwargs.get("error_code") == 8:  # OVERFLOW
                        self.hw_overflows += 1

                self._device.receive(
                    _usrp_sink,
                    config,
                    sample_count=0,
                    cancel_event=self._cancel_event,
                    metadata_callback=_meta_cb,
                    block_samples=32768,
                )

            elif self.sdr_type == "sim":
                # 仿真线程：定时产生带已知正频偏单音的 IQ 样本
                chunk_samples = 32768
                chunk_duration = chunk_samples / self.actual_sample_rate
                while not self._cancel_event.is_set():
                    t0 = time.monotonic()
                    # 生成单音频偏 +1.5MHz 和 -2.0MHz 双载波
                    sim_iq = generate_test_tone_iq(
                        sample_rate=self.actual_sample_rate,
                        num_samples=chunk_samples,
                        tone_offset_hz=1500000.0,
                        amplitude_dbfs=-12.0,
                        noise_floor_dbfs=-85.0,
                    )
                    raw_bytes = sim_iq.tobytes()
                    try:
                        self._rx_queue.put_nowait(raw_bytes)
                    except queue.Full:
                        self._on_rx_overflow()

                    elapsed = time.monotonic() - t0
                    sleep_time = chunk_duration - elapsed
                    if sleep_time > 0.001:
                        time.sleep(sleep_time)

        except Exception as err:
            if not self._cancel_event.is_set():
                logger.error("实时频谱接收线程异常: %s", err, exc_info=True)
                self.state = "error"
                self.error_code = "STREAM_ERROR"
                self.error_message = f"SDR 接收中断: {err}"
        finally:
            self._cancel_event.set()

    def _dsp_worker(self):
        """DSP 处理线程：完成加窗、双边 FFT、相干归一化、平均并限速分发。"""
        iq_format = "fc32" if self.sdr_type in ("usrp", "sim") else "cs8"
        accumulated_samples = np.zeros(0, dtype=np.complex64)
        previous_powers: np.ndarray | None = None

        last_dispatch_time = time.monotonic()
        frame_counter = 0
        fps_timer = time.monotonic()

        while not self._cancel_event.is_set():
            try:
                raw_chunk = self._rx_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            complex_chunk = convert_raw_iq_to_complex(raw_chunk, iq_format=iq_format)
            accumulated_samples = np.concatenate([accumulated_samples, complex_chunk])

            # 保持累积缓冲区有界 (保留最新至多 4*N_FFT 样本)
            max_accum = max(self.n_fft * 4, 65536)
            if len(accumulated_samples) > max_accum:
                accumulated_samples = accumulated_samples[-max_accum:]

            if len(accumulated_samples) < self.n_fft:
                continue

            now = time.monotonic()
            min_interval = 1.0 / max(1.0, min(self.fps_limit, 30.0))
            if now - last_dispatch_time < min_interval:
                # 显示限速期间仍可执行功率平滑，不直接跳过
                continue

            avg_weight = 0.0 if self.fft_average <= 1 else (1.0 - 1.0 / self.fft_average)
            try:
                frame = compute_spectrum(
                    samples=accumulated_samples,
                    n_fft=self.n_fft,
                    sample_rate=self.actual_sample_rate,
                    center_freq=self.actual_center_freq,
                    window_name=self.window,
                    previous_powers=previous_powers,
                    avg_weight=avg_weight,
                )
            except Exception as dsp_err:
                logger.warning("计算频谱异常: %s", dsp_err)
                continue

            previous_powers = np.array(frame["powers"], dtype=np.float32)
            last_dispatch_time = now
            frame_counter += 1

            if now - fps_timer >= 1.0:
                self.fps = round(frame_counter / (now - fps_timer), 1)
                frame_counter = 0
                fps_timer = now

            # 组装完整帧载荷
            frame["session_id"] = self.session_id
            frame["timestamp"] = time.time()
            frame["fps"] = self.fps
            frame["hw_overflows"] = self.hw_overflows
            frame["dsp_dropped_frames"] = self.dsp_dropped_frames
            frame["client_dropped_frames"] = self.client_dropped_frames
            frame["paused"] = self._is_paused

            with self._frame_lock:
                self._latest_frame = frame
                # 向所有连接的 WebSocket 订阅队列推送最新帧 (有界淘汰)
                for sub_q in list(self._frame_subscribers):
                    try:
                        sub_q.put_nowait(frame)
                    except asyncio.QueueFull:
                        self.client_dropped_frames += 1
                        try:
                            # 弹出过期旧帧，推入最新帧，保证恢复时展示最新数据
                            sub_q.get_nowait()
                            sub_q.put_nowait(frame)
                        except Exception:
                            pass

    def update_parameters(self, req: SpectrumUpdateRequest) -> dict[str, Any]:
        """动态更新频谱运行参数，与工作线程安全协调。"""
        results: dict[str, Any] = {}

        # 1. 动态调谐中心频率
        if req.center_freq is not None and abs(req.center_freq - self.actual_center_freq) > 1.0:
            new_freq = float(req.center_freq)
            if self.sdr_type == "hackrf" and self._device and self._device.handle.value:
                self._device.api.check(
                    self._device.api.hackrf_set_freq(self._device.handle, int(new_freq)),
                    "hackrf_set_freq",
                )
            elif self.sdr_type == "usrp" and self._device and self._device.handle.value:
                with self._device.command_lock:
                    from flrc_modem.sdr.usrp.abi import TuneRequest, TuneResult, UHDTuneRequestPolicy

                    tune_req = TuneRequest(
                        target_freq=new_freq,
                        rf_freq_policy=UHDTuneRequestPolicy.AUTO,
                        rf_freq=0.0,
                        dsp_freq_policy=UHDTuneRequestPolicy.AUTO,
                        dsp_freq=0.0,
                        args=b"",
                    )
                    tune_res = TuneResult()
                    self._device.api.check(
                        self._device.api.dll.uhd_usrp_set_rx_freq(
                            self._device.handle, C.byref(tune_req), C.c_size_t(0), C.byref(tune_res)
                        ),
                        "uhd_usrp_set_rx_freq",
                    )
            self.actual_center_freq = new_freq
            results["center_freq"] = new_freq

        # 2. 动态调节 HackRF 增益
        if self.sdr_type == "hackrf" and self._device and self._device.handle.value:
            if req.lna_gain is not None:
                val = max(0, min(40, (req.lna_gain // 8) * 8))
                self._device.api.check(
                    self._device.api.hackrf_set_lna_gain(self._device.handle, val),
                    "hackrf_set_lna_gain",
                )
                results["lna_gain"] = val
            if req.vga_gain is not None:
                val = max(0, min(62, (req.vga_gain // 2) * 2))
                self._device.api.check(
                    self._device.api.hackrf_set_vga_gain(self._device.handle, val),
                    "hackrf_set_vga_gain",
                )
                results["vga_gain"] = val
            if req.amp is not None:
                self._device.api.check(
                    self._device.api.hackrf_set_amp_enable(self._device.handle, int(req.amp)),
                    "hackrf_set_amp_enable",
                )
                results["amp"] = req.amp

        # 3. 动态调节 USRP 增益
        if self.sdr_type == "usrp" and self._device and self._device.handle.value:
            if req.gain is not None:
                with self._device.command_lock:
                    self._device.api.check(
                        self._device.api.dll.uhd_usrp_set_rx_gain(
                            self._device.handle, C.c_double(req.gain), C.c_size_t(0), b""
                        ),
                        "uhd_usrp_set_rx_gain",
                    )
                results["gain"] = req.gain

        # 4. DSP 参数动态修改
        if req.fft_average is not None:
            self.fft_average = req.fft_average
            results["fft_average"] = req.fft_average
        if req.fps_limit is not None:
            self.fps_limit = req.fps_limit
            results["fps_limit"] = req.fps_limit
        if req.dynamic_range_db is not None:
            self.dynamic_range_db = req.dynamic_range_db
            results["dynamic_range_db"] = req.dynamic_range_db
        if req.ref_level_dbfs is not None:
            self.ref_level_dbfs = req.ref_level_dbfs
            results["ref_level_dbfs"] = req.ref_level_dbfs

        return results

    def pause_display(self):
        """暂停显示：后台继续有界接收，前台冻结刷新。"""
        self._is_paused = True
        self.state = "paused"

    def resume_display(self):
        """恢复显示：立即向前端分发最新采集数据。"""
        self._is_paused = False
        self.state = "running"

    def register_subscriber(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        """为 WebSocket 客户端创建有界数据队列 (maxsize=2)。"""
        sub_queue = asyncio.Queue(maxsize=2)
        with self._frame_lock:
            self._frame_subscribers.add(sub_queue)
            # 若有当前最新帧，立即推入首帧，方便前端立即可视化
            if self._latest_frame:
                try:
                    sub_queue.put_nowait(self._latest_frame)
                except asyncio.QueueFull:
                    pass
        return sub_queue

    def unregister_subscriber(self, sub_queue: asyncio.Queue):
        with self._frame_lock:
            self._frame_subscribers.discard(sub_queue)

    def get_status(self) -> SpectrumStatusResponse:
        """获取当前会话结构化状态快照。"""
        rbw = self.actual_sample_rate / self.n_fft if self.n_fft > 0 else 0.0
        return SpectrumStatusResponse(
            session_id=self.session_id,
            state=self.state,
            device_type=self.sdr_type,
            device_id=self.device_id,
            device_name=self.device_name,
            actual_center_freq=self.actual_center_freq,
            actual_sample_rate=self.actual_sample_rate,
            actual_master_clock=self.actual_master_clock,
            n_fft=self.n_fft,
            rbw_hz=rbw,
            fps=self.fps,
            hw_overflows=self.hw_overflows,
            dsp_dropped_frames=self.dsp_dropped_frames,
            client_dropped_frames=self.client_dropped_frames,
            error_code=self.error_code,
            error_message=self.error_message,
        )

    def stop(self):
        """安全停止采集工作线程并释放底层设备句柄与锁。"""
        if self.state in ("stopped",):
            return
        logger.info("正在停止实时频谱会话 %s", self.session_id)
        self._cancel_event.set()
        self.cleanup()
        self.state = "stopped"

    def cleanup(self):
        """执行幂等的设备与互斥锁清理策略。"""
        self._cancel_event.set()

        # 等待工作线程安全退出
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        if self._dsp_thread and self._dsp_thread.is_alive():
            self._dsp_thread.join(timeout=1.0)

        # 释放 SDR 硬件句柄
        if self._device is not None:
            try:
                if hasattr(self._device, "close"):
                    self._device.close()
                elif hasattr(self._device, "__exit__"):
                    self._device.__exit__(None, None, None)
            except Exception as err:
                logger.warning("关闭 SDR 硬件句柄时忽略告警: %s", err)
            self._device = None

        # 释放全局互斥锁
        try:
            get_device_manager().release_device(self.session_id)
        except Exception as err:
            logger.warning("释放设备互斥锁时忽略告警: %s", err)
