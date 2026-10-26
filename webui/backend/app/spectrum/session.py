"""Bounded RX/DSP streaming with explicit hardware ownership and loop-safe delivery."""
from __future__ import annotations

import asyncio
import ctypes as C
from dataclasses import replace
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

from flrc_modem.io.iq import decode_samples
from flrc_modem.log import WideEvent
from flrc_modem.realtime.windows import IQBlockQueue, WindowQueue

from ..devices.manager import get_device_manager
from ..paths import PROJECT_ROOT
from ..storage.db import get_db
from ..events.hub import get_event_hub
from .dsp import compute_spectrum, convert_raw_iq_to_complex, generate_test_tone_iq
from .demod_config import build_demod_config, load_demod_template
from .hackrf_receiver import HackRFSpectrumReceiver
from .files import FileSpectrumSource
from .history import IQHistory
from .selection import export_selection
from .schemas import SpectrumStartRequest, SpectrumStatusResponse, SpectrumUpdateRequest

logger = logging.getLogger(__name__)


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
        self._file_source = FileSpectrumSource(request.iq_path, request.iq_format, request.n_fft) if request.sdr == "file" else None
        self._resource_lock = threading.Lock()
        self._sample_bytes = 2 if request.sdr == "hackrf" else 8
        self.iq_history = IQHistory(self._sample_bytes)
        self._next_host_timestamp = None
        self._rx_queue = IQBlockQueue(8, self._sample_bytes)
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
        self._record_lock = threading.RLock()
        self._record_sink = None
        self._is_recording = False
        self._record_start_time = 0.0
        self._record_duration_s = None
        self._recorded_bytes = 0
        self._record_rel_path = None
        self._record_full_path = None
        self._last_record_status = None

        # 实时解调状态与解调队列
        self.demod_enabled = bool(request.demod_enabled)
        self.demod_center_freq = request.demod_center_freq
        self.demod_bit_rate = request.bit_rate
        self.demod_coding_rate = request.coding_rate
        self.demod_sync_word = request.sync_word
        self.demod_crc_initial = request.crc_initial
        self.demod_protocol = request.protocol or "auto"
        self.demod_packets_count = 0
        self.demod_dropped_chunks = 0
        self._demod_thread = None
        self._demod_queue = IQBlockQueue(32, self._sample_bytes)
        self._demod_gap = False
        self._demod_lock = threading.Lock()
        self._demod_template_values = load_demod_template(request.physical_template)
        self._demod_revision = 0
        if request.demod_enabled or request.physical_template:
            build_demod_config(request, self.actual_center_freq, self.actual_sample_rate, self._demod_template_values)

    def start(self):
        logger.info('%s: 启动频谱 sdr=%s frequency=%s sample_rate=%s demod=%s template=%s',
                    self.session_id, self.sdr_type, self.request.center_freq, self.request.sample_rate,
                    self.demod_enabled, self.request.physical_template)
        if self.sdr_type in ("usrp", "hackrf"):
            manager = get_device_manager()
            selector = manager.resolve_selector(self.sdr_type, self.request.model_dump())
            acquired, error = manager.acquire_device(self.sdr_type, self.session_id, selector)
            if not acquired:
                raise RuntimeError(error or "设备已被其他任务占用")
        try:
            self._init_hardware()
            self._cancel_event.clear()
            self._next_host_timestamp = None
            self._ready.clear()
            self._last_rx_activity = time.monotonic()
            self.error_code = self.error_message = None
            self.state = "running"
            self._dsp_thread = threading.Thread(target=self._dsp_worker, daemon=True, name=f"spectrum-dsp-{self.session_id}")
            self._rx_thread = threading.Thread(target=self._rx_worker, daemon=True, name=f"spectrum-rx-{self.session_id}")
            self._demod_thread = threading.Thread(target=self._demod_worker, daemon=True, name=f"spectrum-demod-{self.session_id}")
            self._dsp_thread.start()
            self._demod_thread.start()
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
            self._device = HackRFSpectrumReceiver(serial=req.serial, device_index=req.device_index)
            self.device_id = req.serial or f"index={req.device_index if req.device_index is not None else 0}"
            self.device_name = "HackRF One"
        elif self.sdr_type == "usrp":
            from flrc_modem.sdr.usrp.device import USRPDevice
            self._device = USRPDevice(device_args=req.args or "", master_clock_rate=req.master_clock_rate)
            self._device.__enter__()
            self.device_id = self._device.device_args or "usrp_default"
            self.device_name = self._device.device_info.get("mboard_name", "USRP")
            self.actual_master_clock = self._device.get_master_clock_rate() or None
        elif self.sdr_type == "file":
            self._device = self._file_source.open()
            self.device_id, self.device_name = str(self._file_source.path), self._file_source.path.name
        else:
            self.device_id, self.device_name = "sim_0", "软件仿真信号源"

    def _enqueue_iq(self, data, timestamp, gap=False):
        if self._cancel_event.is_set():
            return
        self._last_rx_activity = time.monotonic()
        gap = gap or self._gap
        if self.sdr_type in ('hackrf', 'sim'):
            # Host callback jitter is not a sample gap. Anchor a continuous
            # sample clock until the receiver explicitly reports lost IQ.
            if self._next_host_timestamp is not None and not gap:
                timestamp = self._next_host_timestamp
            self._next_host_timestamp = timestamp + len(data) / (self._sample_bytes * self.actual_sample_rate)
        self.iq_history.append(data, timestamp, self.actual_sample_rate, self.actual_center_freq, gap)
        if self._is_recording:
            self._write_record_chunk(data)
        # Hardware gaps apply to both consumers, even if one queue is full.
        if self.demod_enabled:
            dropped_before_demod = self._demod_queue.dropped_blocks
            self._demod_queue.put_latest(data, timestamp, gap or self._demod_gap)
            # Preserve session totals across hardware restarts.
            self.demod_dropped_chunks += self._demod_queue.dropped_blocks - dropped_before_demod
            self._demod_gap = False
        dropped_before = self._rx_queue.dropped_blocks
        self._rx_queue.put_latest(data, timestamp, gap)
        self.dsp_dropped_frames += self._rx_queue.dropped_blocks - dropped_before
        self._gap = False
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
                dropped_chunks = 0

                def on_open(selected):
                    self.device_id = selected.get('serial') or f"index={selected.get('index', req.device_index)}"

                def on_chunk(data, timestamp, gap, dropped):
                    nonlocal dropped_chunks
                    self.dsp_dropped_frames += max(0, dropped - dropped_chunks)
                    dropped_chunks = dropped
                    self._enqueue_iq(data, timestamp, gap)

                self._device.receive(on_chunk, config, cancel_event=self._cancel_event, on_open=on_open)
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
            elif self.sdr_type == "file":
                self._receive_file()
            else:
                sim_packet_cache = None
                last_packet_injection = 0.0
                while not self._cancel_event.is_set():
                    started = time.monotonic()
                    iq = generate_test_tone_iq(self.actual_sample_rate, 32768,
                                              self.actual_sample_rate * .1875, -12, -85)
                    now_sec = time.monotonic()
                    if self.demod_enabled and now_sec - last_packet_injection >= 1.5:
                        if sim_packet_cache is None:
                            try:
                                from flrc_modem.phy.encoder import encode_packet
                                from flrc_modem.modulation import ModulationConfig, iter_signal
                                fixture_path = PROJECT_ROOT / "tests/fixtures/dk32s_packet.json"
                                if fixture_path.exists():
                                    fix = json.loads(fixture_path.read_text())
                                    pkt = encode_packet(bytes.fromhex(fix["payload_hex"]), bytes.fromhex(fix["sync_hex"]))
                                    mod = ModulationConfig(sample_rate=self.actual_sample_rate, repeat=1, guard_seconds=0.002)
                                    sim_packet_cache = np.concatenate(list(iter_signal(pkt.bits, mod)))
                            except Exception as err:
                                logger.debug("加载仿真 FLRC 信号失败: %s", err)
                        if sim_packet_cache is not None:
                            target_offset = (self.demod_center_freq - self.actual_center_freq) if self.demod_center_freq else 0.0
                            if abs(target_offset) > 1.0:
                                t = np.arange(len(sim_packet_cache)) / self.actual_sample_rate
                                shifted = sim_packet_cache * np.exp(1j * 2 * np.pi * target_offset * t)
                            else:
                                shifted = sim_packet_cache
                            n_to_add = min(len(iq), len(shifted))
                            iq[:n_to_add] += (shifted[:n_to_add] * 0.8).astype(np.complex64)
                            last_packet_injection = now_sec
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

    def _receive_file(self):
        origin, started = time.time(), time.monotonic()
        samples, pass_samples = 0, 0
        while not self._cancel_event.is_set():
            raw = self._device.read()
            if not raw:
                if not pass_samples:
                    raise ValueError("IQ 文件在回放期间已清空或截断")
                # Flush the partial decode window before the next file epoch.
                if self.demod_enabled:
                    self._demod_queue.put_latest(b"", origin + samples / self.actual_sample_rate)
                self._device.rewind()
                pass_samples = 0
                continue
            # The file boundary resets decoding, while display time stays continuous.
            if not pass_samples:
                self._demod_gap = True
            self._enqueue_iq(raw, origin + samples / self.actual_sample_rate)
            count = len(raw) // 8  # File input is normalized to complex64.
            samples += count
            pass_samples += count
            self._cancel_event.wait(max(0, samples / self.actual_sample_rate - (time.monotonic() - started)))

    def _fail(self, code, message):
        logger.error("%s: %s", self.session_id, message)
        self._cancel_event.set()
        self._disable_demod()
        self.state, self.error_code, self.error_message = "error", code, message

    def _check_rx_health(self):
        if time.monotonic() - self._last_rx_activity > 5:
            raise TimeoutError("超过 5 秒未收到 IQ 数据，请检查设备连接")

    def _dsp_worker(self):
        try:
            self._process_iq()
        except Exception as error:
            self._fail("DSP_ERROR", f"频谱计算中断: {error}")

    def _demod_worker(self):
        try:
            self._process_demod()
        except Exception as error:
            logger.exception("实时解调通道异常中断: %s", error)

    def _get_demod_config_key(self):
        with self._demod_lock:
            return (
                self._demod_revision,
                self.demod_center_freq,
                self.demod_bit_rate,
                self.demod_coding_rate,
                self.demod_sync_word,
                self.demod_crc_initial,
                self.demod_protocol,
                self.actual_center_freq,
                self.actual_sample_rate,
            )

    def _build_demod_pipeline(self):
        from flrc_modem.pipeline import WindowDecoder
        from flrc_modem.parsing.api import load_parser

        with self._demod_lock:
            request = self.request
            demod_cfg = build_demod_config(request, self.actual_center_freq, self.actual_sample_rate,
                                           self._demod_template_values)
        protocol = request.protocol
        decoder = WindowDecoder(demod_cfg)

        if protocol in ("dk32s", "dk32s-multi", "datalink"):
            parser = load_parser(protocol)
        elif protocol == "auto":
            try:
                dk32s_p = load_parser("dk32s")
                datalink_p = load_parser("datalink")
                def parser(frame):
                    for p in (dk32s_p, datalink_p):
                        try:
                            res = p(frame)
                            if res:
                                return res
                        except Exception:
                            pass
                    return None
            except Exception:
                parser = None
        else:
            parser = None

        return decoder, parser, demod_cfg

    def _process_demod(self):
        from flrc_modem.io.output import build_record
        windows = None
        epoch = None
        current_decoder = None
        current_parser = None
        current_cfg_key = None
        demod_cfg = None

        while not self._cancel_event.is_set():
            if not self.demod_enabled:
                try:
                    self._demod_queue.get(timeout=0.05)
                except queue.Empty:
                    pass
                windows = None
                current_cfg_key = None
                continue

            cfg_key = self._get_demod_config_key()
            if cfg_key != current_cfg_key or current_decoder is None:
                try:
                    current_decoder, current_parser, demod_cfg = self._build_demod_pipeline()
                    # Native HackRF blocks are cs8; USRP/simulation deliver cf32.
                    input_format = "cs8" if self.sdr_type == "hackrf" else "cf32"
                    # Assembly and decode share a thread; raw IQ is already
                    # bounded upstream, so no second pending window backlog.
                    windows = WindowQueue(replace(demod_cfg, iq_format=input_format),
                                          depth=2)
                    current_cfg_key = cfg_key
                    epoch = None
                except Exception as err:
                    logger.error("构建解调管道失败: %s", err)
                    time.sleep(0.1)
                    continue

            try:
                raw, timestamp, gap = self._demod_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            file_end = self.sdr_type == "file" and not raw
            blocks = windows.iter_finish() if file_end else windows.iter_block(raw, timestamp, discontinuity=gap)
            for window in blocks:
                try:
                    if self._cancel_event.is_set():
                        break
                    # A new epoch may move the hardware clock backwards. Do not
                    # deduplicate against packets from the previous epoch.
                    if epoch is not None and window.epoch != epoch:
                        current_decoder.recent.clear()
                    epoch = window.epoch
                    window_iq = decode_samples(windows.view(window), input_format)
                    for frame in current_decoder.decode(window_iq, window.start_time_s):
                        record = build_record(frame, current_parser)
                        self.demod_packets_count += 1
                        self._publish_packet(record)
                except Exception as err:
                    logger.debug("解调窗口失败: %s", err)
                finally:
                    windows.release(window)
            if file_end:
                current_cfg_key = None

    def _process_iq(self):
        iq_format = "cs8" if self.sdr_type == "hackrf" else "fc32"
        accumulated = np.empty(0, np.complex64)
        previous = None
        last_dispatch = time.monotonic()
        frame_count, fps_timer = 0, last_dispatch
        gap_pending = False
        file_segment, file_energy = None, -1.0
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
            if self.sdr_type == "file" and len(accumulated) >= self.n_fft:
                # Keep an actual signal slice from this display interval; selecting
                # only its last FFT window would repeatedly miss short file bursts.
                count = len(accumulated) // self.n_fft
                segments = accumulated[-count * self.n_fft:].reshape(count, self.n_fft)
                energies = np.sum(segments.real**2 + segments.imag**2, axis=1)
                index = int(np.argmax(energies))
                if energies[index] >= file_energy:
                    file_segment, file_energy = segments[index].copy(), float(energies[index])
            now = time.monotonic()
            if len(accumulated) < self.n_fft or now - last_dispatch < 1 / self.fps_limit:
                continue
            weight = 0 if self.fft_average <= 1 else 1 - 1 / self.fft_average
            frame = compute_spectrum(file_segment if self.sdr_type == "file" else accumulated, self.n_fft, self.actual_sample_rate,
                                     self.actual_center_freq, self.window, previous, weight)
            file_segment, file_energy = None, -1.0
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
            frame['iq_history'] = self.iq_history.status()
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

    def _publish_packet(self, record):
        if self._cancel_event.is_set() or not self.demod_enabled:
            return
        msg = {
            "type": "demod_packet",
            "session_id": self.session_id,
            "packet": record,
        }
        with self._frame_lock:
            for sub_queue, (loop, _) in list(self._frame_subscribers.items()):
                try:
                    loop.call_soon_threadsafe(self._deliver_packet, sub_queue, msg)
                except RuntimeError:
                    pass

    def _deliver_packet(self, sub_queue, msg):
        with self._frame_lock:
            subscription = self._frame_subscribers.get(sub_queue)
            if subscription is None or self._cancel_event.is_set() or not self.demod_enabled:
                return
            try:
                if sub_queue.full():
                    try:
                        sub_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                sub_queue.put_nowait(msg)
            except Exception:
                pass

    def update_parameters(self, req: SpectrumUpdateRequest):
        if self.state not in ("running", "paused"):
            raise ValueError("采集会话已停止或发生错误")
        values = req.model_dump(exclude_unset=True)
        # Validate the complete resulting hardware configuration before stopping.
        updated = SpectrumStartRequest.model_validate({**self.request.model_dump(), **values})
        template_values = (load_demod_template(updated.physical_template) if 'physical_template' in values
                           else self._demod_template_values)
        if updated.demod_enabled or updated.physical_template:
            build_demod_config(updated, updated.center_freq, self.actual_sample_rate, template_values)
        hardware_fields = {"center_freq", "gain"} if self.sdr_type == "usrp" else {"center_freq", "lna_gain", "vga_gain", "amp"}
        restarted = any(key in hardware_fields and value != getattr(self.request, key) for key, value in values.items())
        logger.info('%s: 更新频谱 restart_receiver=%s parameters=%s', self.session_id, restarted,
                    {key: value for key, value in values.items() if key not in ('args', 'serial')})
        if restarted:
            paused = self._is_paused
            self._restarting = True
            try:
                self.stop()
                self._minimum_sequence = self._sequence + 1
                self._apply_demod_request(updated, template_values)
                self.actual_center_freq, self.actual_sample_rate = updated.center_freq, updated.sample_rate
                self._rx_queue = IQBlockQueue(8, self._sample_bytes)
                self._demod_queue = IQBlockQueue(32, self._sample_bytes)
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
        self._apply_demod_request(updated, template_values)
        for key in ("fft_average", "fps_limit", "dynamic_range_db", "ref_level_dbfs"):
            setattr(self, key, getattr(updated, key))
        return {**values, "restarted": restarted, "status": self.get_status().model_dump()}

    def _apply_demod_request(self, request, template_values):
        with self._demod_lock:
            if request.demod_enabled != self.demod_enabled:
                self._demod_revision += 1
                self._demod_gap = True
            self.request = request
            if template_values != self._demod_template_values:
                self._demod_revision += 1
            self._demod_template_values = template_values
            self.demod_center_freq = request.demod_center_freq
            self.demod_bit_rate = request.bit_rate
            self.demod_coding_rate = request.coding_rate
            self.demod_sync_word = request.sync_word
            self.demod_crc_initial = request.crc_initial
            self.demod_protocol = request.protocol
            self.demod_enabled = request.demod_enabled

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
        if frame.get("type") == "demod_packet":
            return True
        return self.state == "running" and frame.get("sequence", 0) >= self._minimum_sequence

    def register_subscriber(self, loop):
        sub_queue = asyncio.Queue(maxsize=64)
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
            client_dropped_frames=self.client_dropped_frames,
            demod_enabled=self.demod_enabled,
            demod_physical_template=self.request.physical_template,
            demod_center_freq=self.demod_center_freq or self.actual_center_freq,
            demod_protocol=self.demod_protocol,
            demod_packets_count=self.demod_packets_count,
            iq_history=self.iq_history.status(),
            error_code=self.error_code, error_message=self.error_message)

    def export_selection(self, request):
        return export_selection(self.iq_history, request, session_id=self.session_id,
                                iq_format='cs8' if self.sdr_type == 'hackrf' else 'cf32',
                                device_type=self.sdr_type, root=PROJECT_ROOT)

    def stop(self):
        logger.info('%s: 停止频谱接收', self.session_id)
        self.cleanup()
        self.state = "stopped"

    def _disable_demod(self):
        with self._demod_lock:
            self.demod_enabled = False
            self.request = self.request.model_copy(update={"demod_enabled": False})

    def cleanup(self):
        self._cancel_event.set()
        self._disable_demod()
        with self._record_lock:
            if self._is_recording:
                self._stop_recording_locked()
        for worker in (self._rx_thread, self._dsp_thread, self._demod_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=3)
                if worker.is_alive():
                    self._fail("STOP_TIMEOUT", "接收线程尚未退出，保留设备占用以防并发访问")
                    raise RuntimeError(self.error_message)
        while True:
            try:
                self._demod_queue.get_nowait()
            except queue.Empty:
                break
        self._release_device()

    def _release_device(self):
        with self._resource_lock:
            if self._device is not None:
                self._device.close()
                self._device = None
            if self.sdr_type in ("usrp", "hackrf"):
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
            iq_format = "cs8" if self.sdr_type == "hackrf" else "cf32"
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
            iq_format = "cs8" if self.sdr_type == "hackrf" else "cf32"
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
                self._stop_recording_locked(error=f"写入 IQ 数据失败：{err}")

    def _stop_recording_locked(self, error: str | None = None):
        if not self._is_recording:
            return
        sink = self._record_sink
        self._record_sink = None
        self._is_recording = False
        duration = max(0.001, time.monotonic() - self._record_start_time)
        failures = [error] if error else []

        if sink is not None:
            try:
                sink.flush()
            except Exception as err:
                failures.append(f"刷新 IQ 文件失败：{err}")
            try:
                # A failed flush must not prevent releasing the file handle.
                sink.close()
            except Exception as err:
                failures.append(f"关闭 IQ 文件失败：{err}")

        iq_format = "cs8" if self.sdr_type == "hackrf" else "cf32"
        sample_bytes = 2 if iq_format == "cs8" else 8
        samples_count = self._recorded_bytes // sample_bytes

        # Publish success only after the file, metadata and registration succeed.
        # Failed captures retain their path for recovery, never a fake run ID.
        self._last_record_status = {
            "is_recording": False,
            "completed": False,
            "duration_s": round(duration, 3),
            "elapsed_s": round(duration, 2),
            "recorded_bytes": self._recorded_bytes,
            "samples_count": samples_count,
            "filepath": self._record_rel_path,
            "run_id": None,
            "sample_rate": self.actual_sample_rate,
            "center_freq": self.actual_center_freq,
            "iq_format": iq_format,
            "error": None,
        }

        def fail(stage: str, message: str):
            self._last_record_status["error"] = message
            WideEvent('spectrum.recording_failed', logger=logger, entry_point='webui.spectrum.record',
                      session_id=self.session_id, filepath=self._record_rel_path,
                      recorded_bytes=self._recorded_bytes, failure_stage=stage).record_error(message).emit(logging.ERROR)

        if failures:
            fail('write' if error else 'close', '；'.join(failures))
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        run_id = f"rec_{uuid.uuid4().hex[:8]}"

        # 写 companion JSON
        try:
            if self._record_full_path is None or not self._record_full_path.is_file():
                raise OSError('录制 IQ 文件不存在')
            companion_path = Path(str(self._record_full_path) + ".json")
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
            fail('metadata', f"保存录制元数据失败：{err}")
            return

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
            }, artifact={
                "stage_id": "rx",
                "path": self._record_rel_path,
                "kind": "input_iq",
                "metadata": {
                    "sample_rate": self.actual_sample_rate,
                    "center_frequency": self.actual_center_freq,
                    "iq_format": iq_format,
                    "samples_count": samples_count,
                    "duration_s": round(duration, 3),
                    "source": "spectrum_recording",
                },
                "created_at": now_iso,
            })
        except Exception as err:
            fail('registration', f"登记录制任务或产物失败：{err}")
            return

        self._last_record_status.update(completed=True, run_id=run_id)
