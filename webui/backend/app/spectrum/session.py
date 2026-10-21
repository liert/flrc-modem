"""Bounded RX/DSP streaming with explicit hardware ownership and loop-safe delivery."""
from __future__ import annotations

import asyncio
import ctypes as C
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import queue
import threading
import time
from typing import Any
import uuid

import numpy as np

from ..devices.manager import get_device_manager
from ..paths import PROJECT_ROOT
from ..storage.db import get_db
from ..events.hub import get_event_hub
from .dsp import compute_spectrum, convert_raw_iq_to_complex, generate_test_tone_iq
from .schemas import SpectrumStartRequest, SpectrumStatusResponse, SpectrumUpdateRequest

logger = logging.getLogger(__name__)


class _QueueSink:
    def __init__(self, session):
        self.session = session

    def write(self, buffer):
        data = bytes(buffer)
        self.session._enqueue_iq(data, time.time() - len(data) / (2 * self.session.actual_sample_rate))
        return len(data)


class SpectrumSession:
    def __init__(self, request: SpectrumStartRequest):
        self.session_id = f"spec_{uuid.uuid4().hex[:8]}"
        self.request = request
        self.sdr_type = request.sdr
        self.state = "idle"
        self.actual_center_freq = request.center_freq
        self.actual_sample_rate = request.sample_rate
        self.actual_master_clock = None
        self.device_id = None
        self.device_name = None
        self.n_fft = request.n_fft
        self.window = request.window
        self.fft_average = request.fft_average
        self.fps_limit = request.fps_limit
        self.dynamic_range_db = request.dynamic_range_db
        self.ref_level_dbfs = request.ref_level_dbfs
        self.hw_overflows = 0
        self.dsp_dropped_frames = 0
        self.client_dropped_frames = 0
        self.fps = 0.0
        self.error_code = None
        self.error_message = None
        self._cancel_event = threading.Event()
        self._ready = threading.Event()
        self._is_paused = False
        self._rx_thread = None
        self._dsp_thread = None
        self._device = None
        self._resource_lock = threading.Lock()
        self._rx_queue = queue.Queue(maxsize=8)
        self._latest_frame = None
        # A single pending event-loop callback per subscriber prevents an unbounded
        # call_soon_threadsafe backlog when the event loop or browser is slow.
        self._frame_subscribers: dict[asyncio.Queue, list] = {}
        self._frame_lock = threading.Lock()
        self._sequence = 0
        self._gap = False
        self._restarting = False
        self._minimum_sequence = 0
        self._last_rx_activity = time.monotonic()
        self._record_lock = threading.Lock()
        self._record_sink = None
        self._is_recording = False
        self._record_start_time = 0.0
        self._record_duration_s = None
        self._recorded_bytes = 0
        self._record_rel_path = None
        self._record_full_path = None
        self._last_record_status = None

    def start(self):
        manager = get_device_manager()
        if self.sdr_type != "sim":
            selector = manager.resolve_selector(self.sdr_type, self.request.model_dump())
            acquired, error = manager.acquire_device(self.sdr_type, self.session_id, selector)
            if not acquired:
                raise RuntimeError(error or "设备已被其他任务占用")
        try:
            self._init_hardware()
            self._cancel_event.clear()
            self._ready.clear()
            self._last_rx_activity = time.monotonic()
            self.error_code = self.error_message = None
            self.state = "running"
            self._dsp_thread = threading.Thread(target=self._dsp_worker, daemon=True, name=f"spectrum-dsp-{self.session_id}")
            self._rx_thread = threading.Thread(target=self._rx_worker, daemon=True, name=f"spectrum-rx-{self.session_id}")
            self._dsp_thread.start()
            self._rx_thread.start()
            # Do not report a successful start before the driver has configured
            # the receiver and actually delivered IQ (including coerced rates).
            if not self._ready.wait(10):
                raise RuntimeError("等待 SDR 接收首块数据超时")
            if self.state == "error":
                raise RuntimeError(self.error_message)
        except Exception as error:
            self.state = "error"
            self.error_code = self.error_code or "DEVICE_OPEN_FAILED"
            self.error_message = str(error)
            self.cleanup()
            raise

    def _init_hardware(self):
        req = self.request
        if self.sdr_type == "hackrf":
            from flrc_modem.sdr.hackrf.device import HackRFDevice
            self._device = HackRFDevice(serial=req.serial, device_index=req.device_index)
            self._device.__enter__()
            selected = self._device.selected_device or {}
            self.device_id = selected.get("serial") or f"index={selected.get('index', req.device_index)}"
            self.device_name = "HackRF One"
        elif self.sdr_type == "usrp":
            from flrc_modem.sdr.usrp.device import USRPDevice
            self._device = USRPDevice(device_args=req.args or "", master_clock_rate=req.master_clock_rate)
            self._device.__enter__()
            self.device_id = self._device.device_args or "usrp_default"
            self.device_name = self._device.device_info.get("mboard_name", "USRP")
            self.actual_master_clock = self._device.get_master_clock_rate() or None
        else:
            self.device_id, self.device_name = "sim_0", "软件仿真信号源"

    def _enqueue_iq(self, data, timestamp, gap=False):
        if self._cancel_event.is_set():
            return
        self._last_rx_activity = time.monotonic()
        if self._is_recording:
            self._write_record_chunk(data)
        block = (data, timestamp, gap or self._gap)
        self._gap = False
        try:
            self._rx_queue.put_nowait(block)
        except queue.Full:
            self.dsp_dropped_frames += 1
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                pass
            # Drop the oldest display data, preserving live rather than stale IQ.
            self._rx_queue.put_nowait((data, timestamp, True))
        self._ready.set()

    def _rx_worker(self):
        try:
            req = self.request
            if self.sdr_type == "hackrf":
                from flrc_modem.sdr.hackrf.config import HackRFConfig
                config = HackRFConfig(int(req.center_freq), sample_rate=req.sample_rate,
                                      lna_gain=16 if req.lna_gain is None else req.lna_gain,
                                      vga_gain=20 if req.vga_gain is None else req.vga_gain,
                                      amp=req.amp, bias_tee=req.bias_tee)
                # The driver defaults to a 30s total timeout; continuous viewing
                # must explicitly disable it. Keep its native USB buffer size.
                self._device.receive(_QueueSink(self), config, sample_count=None,
                                     cancel_event=self._cancel_event, timeout=None,
                                     health_check=self._check_rx_health, health_interval=1)
            elif self.sdr_type == "usrp":
                from flrc_modem.sdr.usrp.config import USRPConfig
                from flrc_modem.sdr.usrp.abi import UHDRxMetadataErrorCode
                config = USRPConfig(frequency_hz=req.center_freq, sample_rate=req.sample_rate,
                                    gain=30.0 if req.gain is None else req.gain,
                                    antenna=req.antenna or "TX/RX", channel=req.channel,
                                    master_clock_rate=req.master_clock_rate, cpu_format="fc32")
                timestamp = [None]
                next_timestamp = [None]
                origin = [None]

                def configured(rate):
                    self.actual_sample_rate = rate
                    freq = C.c_double()
                    self._device.api.check(self._device.api.dll.uhd_usrp_get_rx_freq(
                        self._device.handle, C.c_size_t(req.channel), C.byref(freq)), "get_rx_freq")
                    self.actual_center_freq = freq.value

                def metadata(has_time, full_secs, frac_secs, sample_count, error_code):
                    if error_code == UHDRxMetadataErrorCode.OVERFLOW:
                        self.hw_overflows += 1
                        self._gap = True
                        next_timestamp[0] = None
                        timestamp[0] = None
                    if has_time and sample_count:
                        hw_time = full_secs + frac_secs
                        if origin[0] is None:
                            origin[0] = time.time() - sample_count / self.actual_sample_rate - hw_time
                        timestamp[0] = origin[0] + hw_time
                        if next_timestamp[0] is not None and abs(timestamp[0] - next_timestamp[0]) > 1.5 / self.actual_sample_rate:
                            self._gap = True
                    else:
                        timestamp[0] = next_timestamp[0]
                    if timestamp[0] is not None and sample_count:
                        next_timestamp[0] = timestamp[0] + sample_count / self.actual_sample_rate

                def sink(data):
                    start = timestamp[0]
                    if start is None:
                        start = time.time() - len(data) / (8 * self.actual_sample_rate)
                    self._enqueue_iq(data, start)

                self._device.receive(sink, config, sample_count=0, cancel_event=self._cancel_event,
                                     rate_callback=configured, metadata_callback=metadata,
                                     block_samples=32768, timeout=.5)
            else:
                while not self._cancel_event.is_set():
                    started = time.monotonic()
                    iq = generate_test_tone_iq(self.actual_sample_rate, 32768,
                                              self.actual_sample_rate * .1875, -12, -85)
                    self._enqueue_iq(iq.tobytes(), time.time())
                    self._cancel_event.wait(max(0, 32768 / self.actual_sample_rate - (time.monotonic() - started)))
            if not self._cancel_event.is_set():
                raise RuntimeError("SDR 接收流意外结束")
        except Exception as error:
            if not self._cancel_event.is_set():
                self._fail("STREAM_ERROR", f"SDR 接收中断: {error}")
        finally:
            self._cancel_event.set()
            self._ready.set()
            # The receive call has returned, so native callbacks/streamers are
            # finished. Only now may another task take ownership of the radio.
            try:
                self._release_device()
            except Exception as error:
                self._fail("DEVICE_CLOSE_FAILED", str(error))

    def _fail(self, code, message):
        logger.error("%s: %s", self.session_id, message)
        self.state, self.error_code, self.error_message = "error", code, message
        self._cancel_event.set()

    def _check_rx_health(self):
        if time.monotonic() - self._last_rx_activity > 5:
            raise TimeoutError("超过 5 秒未收到 IQ 数据，请检查设备连接")

    def _dsp_worker(self):
        try:
            self._process_iq()
        except Exception as error:
            self._fail("DSP_ERROR", f"频谱计算中断: {error}")

    def _process_iq(self):
        iq_format = "cs8" if self.sdr_type == "hackrf" else "fc32"
        accumulated = np.empty(0, np.complex64)
        previous = None
        last_dispatch = time.monotonic()
        frame_count, fps_timer = 0, last_dispatch
        gap_pending = False
        while not self._cancel_event.is_set():
            try:
                raw, timestamp, gap = self._rx_queue.get(timeout=.05)
            except queue.Empty:
                continue
            if gap:
                accumulated = np.empty(0, np.complex64)
                previous = None
                gap_pending = True
            chunk = convert_raw_iq_to_complex(raw, iq_format)
            accumulated = np.concatenate((accumulated, chunk))[-max(self.n_fft * 4, 65536):]
            now = time.monotonic()
            if len(accumulated) < self.n_fft or now - last_dispatch < 1 / self.fps_limit:
                continue
            weight = 0 if self.fft_average <= 1 else 1 - 1 / self.fft_average
            frame = compute_spectrum(accumulated, self.n_fft, self.actual_sample_rate,
                                     self.actual_center_freq, self.window, previous, weight)
            previous = np.asarray(frame["powers"])
            last_dispatch = now
            self._sequence += 1
            frame_count += 1
            if now - fps_timer >= 1:
                self.fps = round(frame_count / (now - fps_timer), 1)
                frame_count, fps_timer = 0, now
            frame.update(session_id=self.session_id, sequence=self._sequence,
                         timestamp=timestamp + len(chunk) / self.actual_sample_rate,
                         timestamp_source="uhd" if self.sdr_type == "usrp" else "host_estimate",
                         discontinuity=gap_pending, fps=self.fps, hw_overflows=self.hw_overflows,
                         dsp_dropped_frames=self.dsp_dropped_frames,
                         client_dropped_frames=self.client_dropped_frames, paused=self._is_paused)
            gap_pending = False
            self._publish(frame)

    def _publish(self, frame):
        with self._frame_lock:
            self._latest_frame = frame
            if self._is_paused:
                return
            for sub_queue, subscription in list(self._frame_subscribers.items()):
                loop, pending = subscription
                if pending:
                    self.client_dropped_frames += 1
                    continue
                subscription[1] = True
                try:
                    loop.call_soon_threadsafe(self._deliver_latest, sub_queue)
                except RuntimeError:
                    self._frame_subscribers.pop(sub_queue, None)

    def _deliver_latest(self, sub_queue):
        # Runs exclusively on the subscriber's asyncio event loop.
        with self._frame_lock:
            subscription = self._frame_subscribers.get(sub_queue)
            if subscription is None:
                return
            subscription[1] = False
            if self._is_paused or self._latest_frame is None:
                return
            if sub_queue.full():
                sub_queue.get_nowait()
                self.client_dropped_frames += 1
            sub_queue.put_nowait(self._latest_frame)

    def update_parameters(self, req: SpectrumUpdateRequest):
        if self.state not in ("running", "paused"):
            raise ValueError("采集会话已停止或发生错误")
        values = req.model_dump(exclude_none=True)
        # Validate the complete resulting hardware configuration before stopping.
        updated = SpectrumStartRequest.model_validate({**self.request.model_dump(), **values})
        hardware_fields = {"center_freq", "gain"} if self.sdr_type == "usrp" else {"center_freq", "lna_gain", "vga_gain", "amp"}
        restarted = any(key in hardware_fields and value != getattr(self.request, key) for key, value in values.items())
        if restarted:
            paused = self._is_paused
            self._restarting = True
            try:
                self.stop()
                self._minimum_sequence = self._sequence + 1
                self.request = updated
                self.actual_center_freq, self.actual_sample_rate = updated.center_freq, updated.sample_rate
                self._rx_queue = queue.Queue(maxsize=8)
                with self._frame_lock:
                    self._latest_frame = None
                    for sub, (loop, _) in self._frame_subscribers.items():
                        loop.call_soon_threadsafe(self._flush_subscriber, sub)
                self._gap = True
                self.start()
                if paused:
                    self.pause_display()
            finally:
                self._restarting = False
        self.request = updated
        for key in ("fft_average", "fps_limit", "dynamic_range_db", "ref_level_dbfs"):
            setattr(self, key, getattr(updated, key))
        return {**values, "restarted": restarted, "status": self.get_status().model_dump()}

    @staticmethod
    def _flush_subscriber(sub):
        while not sub.empty():
            sub.get_nowait()

    @property
    def has_subscribers(self):
        with self._frame_lock:
            return bool(self._frame_subscribers)

    def pause_display(self):
        if self.state != "running":
            raise ValueError("仅运行中的会话可以暂停")
        self._is_paused, self.state = True, "paused"

    def resume_display(self):
        if self.state != "paused":
            raise ValueError("仅暂停中的会话可以恢复")
        with self._frame_lock:
            frame = self._latest_frame
            if frame:
                self._minimum_sequence = frame.get("sequence", 0)
        self._is_paused, self.state = False, "running"
        if frame:
            self._publish({**frame, "paused": False, "discontinuity": True})

    def accepts_display_frame(self, frame):
        return self.state == "running" and frame.get("sequence", 0) >= self._minimum_sequence

    def register_subscriber(self, loop):
        sub_queue = asyncio.Queue(maxsize=2)
        with self._frame_lock:
            self._frame_subscribers[sub_queue] = [loop, False]
        return sub_queue

    def unregister_subscriber(self, sub_queue):
        with self._frame_lock:
            self._frame_subscribers.pop(sub_queue, None)

    def get_status(self):
        state = "paused" if self._is_paused else "running"
        return SpectrumStatusResponse(session_id=self.session_id, state=state if self._restarting and self.state != "error" else self.state,
            device_type=self.sdr_type, device_id=self.device_id, device_name=self.device_name,
            actual_center_freq=self.actual_center_freq, actual_sample_rate=self.actual_sample_rate,
            actual_master_clock=self.actual_master_clock, n_fft=self.n_fft,
            rbw_hz=self.actual_sample_rate / self.n_fft, fps=self.fps,
            hw_overflows=self.hw_overflows, dsp_dropped_frames=self.dsp_dropped_frames,
            client_dropped_frames=self.client_dropped_frames, error_code=self.error_code, error_message=self.error_message)

    def stop(self):
        self.cleanup()
        self.state = "stopped"

    def cleanup(self):
        self._cancel_event.set()
        with self._record_lock:
            if self._is_recording:
                self._stop_recording_locked()
        for worker in (self._rx_thread, self._dsp_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=3)
                if worker.is_alive():
                    self._fail("STOP_TIMEOUT", "接收线程尚未退出，保留设备占用以防并发访问")
                    raise RuntimeError(self.error_message)
        self._release_device()

    def _release_device(self):
        with self._resource_lock:
            if self._device is not None:
                self._device.close()
                self._device = None
            get_device_manager().release_device(self.session_id)

    # ==========================================
    # 流式快速录制引擎 (Live Spectrum Recording)
    # ==========================================
    def start_recording(self, duration_s: float | None = 2.0, filename_prefix: str | None = "spectrum_capture") -> dict[str, Any]:
        with self._record_lock:
            if self.state not in ("running", "paused"):
                raise RuntimeError("频谱采集未就绪或未处于运行状态，无法开始录制")
            if self._is_recording:
                raise RuntimeError("当前已在录制中，请等待完成或手动停止当前录制")

            prefix = (filename_prefix or "spectrum_capture").strip().replace("/", "_").replace("\\", "_")
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            iq_format = "cs8" if self.sdr_type == "hackrf" else ("fc32" if self.sdr_type == "usrp" else "cs8")
            ext = "cs8" if iq_format == "cs8" else "cf32"
            filename = f"{prefix}_{timestamp_str}.{ext}"

            captures_dir = PROJECT_ROOT / "outputs" / "captures"
            captures_dir.mkdir(parents=True, exist_ok=True)
            full_path = captures_dir / filename
            rel_path = full_path.relative_to(PROJECT_ROOT)

            try:
                self._record_sink = open(full_path, "wb", buffering=1024 * 1024)
            except Exception as err:
                raise RuntimeError(f"无法创建录制文件: {err}")

            self._is_recording = True
            self._record_start_time = time.monotonic()
            self._record_duration_s = duration_s if (duration_s and duration_s > 0) else None
            self._recorded_bytes = 0
            self._record_rel_path = str(rel_path).replace("\\", "/")
            self._record_full_path = full_path
            self._last_record_status = None

            return self.get_recording_status()

    def stop_recording(self) -> dict[str, Any]:
        with self._record_lock:
            if not self._is_recording and not self._last_record_status:
                return {"is_recording": False, "completed": False}
            if self._is_recording:
                self._stop_recording_locked()
            return self.get_recording_status()

    def get_recording_status(self) -> dict[str, Any]:
        with self._record_lock:
            iq_format = "cs8" if self.sdr_type == "hackrf" else ("fc32" if self.sdr_type == "usrp" else "cs8")
            sample_bytes = 2 if iq_format == "cs8" else 8
            if self._is_recording:
                now = time.monotonic()
                elapsed = now - self._record_start_time
                return {
                    "is_recording": True,
                    "completed": False,
                    "duration_s": self._record_duration_s,
                    "elapsed_s": round(elapsed, 2),
                    "recorded_bytes": self._recorded_bytes,
                    "samples_count": self._recorded_bytes // sample_bytes,
                    "filepath": self._record_rel_path,
                    "run_id": None,
                    "sample_rate": self.actual_sample_rate,
                    "center_freq": self.actual_center_freq,
                    "iq_format": iq_format,
                    "error": None,
                }
            if self._last_record_status:
                return dict(self._last_record_status)
            return {
                "is_recording": False,
                "completed": False,
                "duration_s": None,
                "elapsed_s": 0.0,
                "recorded_bytes": 0,
                "samples_count": 0,
                "filepath": None,
                "run_id": None,
                "sample_rate": self.actual_sample_rate,
                "center_freq": self.actual_center_freq,
                "iq_format": iq_format,
                "error": None,
            }

    def _write_record_chunk(self, data: bytes):
        with self._record_lock:
            if not self._is_recording or self._record_sink is None:
                return
            try:
                self._record_sink.write(data)
                self._recorded_bytes += len(data)
                now = time.monotonic()
                if self._record_duration_s and (now - self._record_start_time) >= self._record_duration_s:
                    self._stop_recording_locked()
            except Exception as err:
                logger.error("写入频谱录制数据失败: %s", err)
                self._stop_recording_locked(error=str(err))

    def _stop_recording_locked(self, error: str | None = None):
        if not self._is_recording:
            return
        sink = self._record_sink
        self._record_sink = None
        self._is_recording = False
        duration = max(0.001, time.monotonic() - self._record_start_time)

        if sink is not None:
            try:
                sink.flush()
                sink.close()
            except Exception as err:
                logger.warning("关闭录制文件流警告: %s", err)

        iq_format = "cs8" if self.sdr_type == "hackrf" else ("fc32" if self.sdr_type == "usrp" else "cs8")
        sample_bytes = 2 if iq_format == "cs8" else 8
        samples_count = self._recorded_bytes // sample_bytes

        if error:
            self._last_record_status = {
                "is_recording": False,
                "completed": False,
                "duration_s": self._record_duration_s,
                "elapsed_s": round(duration, 2),
                "recorded_bytes": self._recorded_bytes,
                "samples_count": samples_count,
                "filepath": self._record_rel_path,
                "run_id": None,
                "sample_rate": self.actual_sample_rate,
                "center_freq": self.actual_center_freq,
                "iq_format": iq_format,
                "error": error,
            }
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        run_id = f"rec_{uuid.uuid4().hex[:8]}"

        # 写 companion JSON
        if self._record_full_path is not None and self._record_full_path.exists():
            companion_path = Path(str(self._record_full_path) + ".json")
            try:
                companion_path.write_text(
                    json.dumps({
                        "sample_rate": self.actual_sample_rate,
                        "center_frequency": self.actual_center_freq,
                        "iq_format": iq_format,
                        "device": self.sdr_type,
                        "samples_count": samples_count,
                        "duration_s": round(duration, 3),
                        "created_at": now_iso,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as err:
                logger.warning("写伴随元数据警告: %s", err)

        # 写入任务记录与产物表，便于信号分析与离线解调直接读取
        try:
            db = get_db()
            db.insert_run({
                "id": run_id,
                "command_id": "rx",
                "name": f"频谱快速录制 ({(self.actual_center_freq / 1e6):.3f}MHz)",
                "command_args": {
                    "output": self._record_rel_path,
                    "frequency": self.actual_center_freq,
                    "sample_rate": self.actual_sample_rate,
                    "iq_format": iq_format,
                    "sdr": self.sdr_type,
                    "duration": round(duration, 2),
                },
                "cli_argv": ["python", "-m", "flrc_modem", "rx", "--frequency", str(self.actual_center_freq),
                             "--sample-rate", str(self.actual_sample_rate), "--output", str(self._record_rel_path)],
                "status": "SUCCEEDED",
                "created_at": now_iso,
                "started_at": now_iso,
                "completed_at": now_iso,
                "exit_code": 0,
                "requires_usrp": self.sdr_type == "usrp",
                "sdr_type": self.sdr_type,
                "summary": {
                    "bytes": self._recorded_bytes,
                    "samples": samples_count,
                    "duration_s": round(duration, 3),
                },
            })
            db.insert_artifact(
                run_id=run_id,
                stage_id="rx",
                path=self._record_rel_path,
                kind="input_iq",
                metadata={
                    "sample_rate": self.actual_sample_rate,
                    "center_frequency": self.actual_center_freq,
                    "iq_format": iq_format,
                    "samples_count": samples_count,
                    "duration_s": round(duration, 3),
                    "source": "spectrum_recording",
                },
                created_at=now_iso,
            )
        except Exception as err:
            logger.warning("注册录制任务产物警告: %s", err)

        self._last_record_status = {
            "is_recording": False,
            "completed": True,
            "run_id": run_id,
            "filepath": self._record_rel_path,
            "recorded_bytes": self._recorded_bytes,
            "samples_count": samples_count,
            "sample_rate": self.actual_sample_rate,
            "center_freq": self.actual_center_freq,
            "iq_format": iq_format,
            "duration_s": round(duration, 3),
            "error": None,
        }
