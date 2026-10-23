"""USRP 硬件句柄上下文管理、射频配置与收发流式传输。"""
import ctypes as C
from dataclasses import asdict, dataclass
import threading
import time
from math import isfinite
from functools import wraps
import numpy as np


def control_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.command_lock:
            return method(self, *args, **kwargs)
    return locked


def tx_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._tx_lock:
            return method(self, *args, **kwargs)
    return locked

from .abi import (
    StreamArgs,
    StreamCmd,
    TuneRequest,
    TuneResult,
    UHDAsyncMetadataEventCode,
    UHDErrorCode,
    UHDRxMetadataErrorCode,
    UHDStreamMode,
    UHDTuneRequestPolicy,
)
from .config import USRPConfig, with_master_clock_rate
from .library import UHDError, UHDLibrary


@dataclass
class USRPStreamStats:
    mode: str
    sample_count: int = 0
    byte_count: int = 0
    overflow_count: int = 0
    underflow_count: int = 0
    elapsed_seconds: float = 0.0
    actual_rate: float = 0.0
    device_info: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class USRPDevice:
    """封装对单个 USRP 设备的连接、配置和收发数据流。"""

    def __init__(self, library: UHDLibrary | None = None, device_args: str = "", master_clock_rate: float | None = None):
        self.device_args = with_master_clock_rate(device_args, master_clock_rate)
        self.requested_master_clock_rate = master_clock_rate
        if self.requested_master_clock_rate is None:
            for part in self.device_args.split(','):
                key, separator, value = part.partition('=')
                if separator and key.strip() == 'master_clock_rate':
                    self.requested_master_clock_rate = float(value)
        if self.requested_master_clock_rate is not None:
            with_master_clock_rate('', self.requested_master_clock_rate)
        self.api = library or UHDLibrary()
        self.handle = C.c_void_p()
        self._lock = threading.Lock()
        self.command_lock = threading.RLock()
        self._tx_lock = threading.RLock()
        self._burst_stream = C.c_void_p()
        self._burst_config = None
        self._burst_rate = 0.0
        self._master_clock_rate = None
        self._clock_lock = threading.Lock()
        self._clock_estimate = None
        self.device_info = {}
        self.last_stats = None

    def __enter__(self):
        if self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备已打开，不可重复进入上下文")
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        """打开 USRP 硬件并查询设备基本信息。"""
        with self._lock:
            if self.handle.value:
                return
            args_bytes = self.device_args.encode("utf-8")
            code = self.api.dll.uhd_usrp_make(C.byref(self.handle), args_bytes)
            self.api.check(code, "uhd_usrp_make")
            try:
                self._clock_estimate = None
                self._query_info()
                if self.requested_master_clock_rate is not None:
                    self._verify_master_clock_rate(self.requested_master_clock_rate)
                elif hasattr(self.api.dll, 'uhd_usrp_get_master_clock_rate'):
                    self._master_clock_rate = self.get_master_clock_rate()
            except BaseException:
                self.api.dll.uhd_usrp_free(C.byref(self.handle))
                self.handle = C.c_void_p()
                self._master_clock_rate = None
                raise

    def close(self):
        """释放底层 USRP 硬件句柄。"""
        with self._lock, self._tx_lock, self.command_lock:
            if self.handle.value:
                if self._burst_stream.value:
                    self.api.check(self.api.dll.uhd_tx_streamer_free(C.byref(self._burst_stream)), "uhd_tx_streamer_free")
                self._burst_config = None
                self._master_clock_rate = None
                self._clock_estimate = None
                code = self.api.dll.uhd_usrp_free(C.byref(self.handle))
                self.handle = C.c_void_p()
                self.api.check(code, "uhd_usrp_free")

    def _query_info(self):
        """读取主板名称及通道数量等信息。"""
        buf = C.create_string_buffer(256)
        self.api.dll.uhd_usrp_get_mboard_name(self.handle, 0, buf, 256)
        mboard = buf.value.decode("utf-8", errors="replace")

        rx_chans = C.c_size_t(0)
        tx_chans = C.c_size_t(0)
        self.api.dll.uhd_usrp_get_rx_num_channels(self.handle, C.byref(rx_chans))
        self.api.dll.uhd_usrp_get_tx_num_channels(self.handle, C.byref(tx_chans))

        self.device_info = {
            "mboard_name": mboard,
            "rx_channels": rx_chans.value,
            "tx_channels": tx_chans.value,
            "device_args": self.device_args,
        }

    def get_pp_string(self) -> str:
        """获取 USRP 设备的完整探测描述信息（类似 uhd_usrp_probe）。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        buf = C.create_string_buffer(16384)
        code = self.api.dll.uhd_usrp_get_pp_string(self.handle, buf, 16384)
        self.api.check(code, "uhd_usrp_get_pp_string")
        return buf.value.decode("utf-8", errors="replace")

    def get_time_now(self, mboard: int = 0) -> float:
        """获取 USRP 设备内部当前的硬件时间（秒）。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        full = C.c_int64(0)
        frac = C.c_double(0.0)
        code = self.api.dll.uhd_usrp_get_time_now(self.handle, C.c_size_t(mboard), C.byref(full), C.byref(frac))
        self.api.check(code, "uhd_usrp_get_time_now")
        return float(full.value) + frac.value

    def set_time_now(self, time_s: float, mboard: int = 0):
        """设置 USRP 设备当前硬件时间。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        full = int(time_s)
        frac = float(time_s - full)
        code = self.api.dll.uhd_usrp_set_time_now(self.handle, C.c_int64(full), C.c_double(frac), C.c_size_t(mboard))
        self.api.check(code, "uhd_usrp_set_time_now")
        self._clock_estimate = None

    def get_time_estimate(self) -> float:
        """Conservative current device time, cached to avoid an RPC per hop.

        A register read may take tens of ms on Windows. Add the full round-trip
        latency so deadline checks never assume that old value is still 'now'.
        This bound selects future deadlines; RX metadata remains the fit clock.
        """
        with self._clock_lock:
            now = time.monotonic()
            if self._clock_estimate is None or now - self._clock_estimate[0] > 0.5:
                before = now
                hw = self.get_time_now()
                now = time.monotonic()
                self._clock_estimate = (now, hw + now - before)
            host, hw_upper = self._clock_estimate
            return hw_upper + (time.monotonic() - host) * 1.0002

    def set_command_time(self, time_s: float, mboard: int = 0):
        """设置后续命令的生效硬件时间戳（Timed Command）。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        full = int(time_s)
        frac = float(time_s - full)
        code = self.api.dll.uhd_usrp_set_command_time(self.handle, C.c_int64(full), C.c_double(frac), C.c_size_t(mboard))
        self.api.check(code, "uhd_usrp_set_command_time")

    def clear_command_time(self, mboard: int = 0):
        """清除定时命令时间，后续命令立即生效。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        code = self.api.dll.uhd_usrp_clear_command_time(self.handle, C.c_size_t(mboard))
        self.api.check(code, "uhd_usrp_clear_command_time")

    @control_locked
    def set_master_clock_rate(self, rate: float, mboard: int = 0):
        """设置 USRP 设备主板的主时钟频率 (MCR)，例如 125e6, 122.88e6, 153.6e6。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        with_master_clock_rate('', rate)
        if mboard == 0 and self._master_clock_rate == rate:
            return
        if abs(self.get_master_clock_rate(mboard) - rate) <= 1.0:
            self._verify_master_clock_rate(rate, mboard)
            return
        if hasattr(self.api.dll, "uhd_usrp_set_master_clock_rate"):
            code = self.api.dll.uhd_usrp_set_master_clock_rate(self.handle, C.c_double(rate), C.c_size_t(mboard))
            self.api.check(code, "uhd_usrp_set_master_clock_rate")
            self._verify_master_clock_rate(rate, mboard)
            self._clock_estimate = None
        else:
            raise UHDError(UHDErrorCode.NOT_IMPLEMENTED, 'UHD 不支持设置主时钟；请在创建设备时传入 master_clock_rate')

    def _verify_master_clock_rate(self, requested: float, mboard: int = 0):
        actual = self.get_master_clock_rate(mboard)
        if not isfinite(actual) or actual <= 0:
            raise UHDError(UHDErrorCode.NOT_IMPLEMENTED, '无法回读 USRP 主时钟，不能确认 master_clock_rate 是否生效')
        suffix = '' if mboard == 0 else f'_mboard{mboard}'
        self.device_info['requested_master_clock_rate' + suffix] = requested
        if mboard == 0:
            self._master_clock_rate = actual
        if abs(actual - requested) > 1.0:
            raise UHDError(UHDErrorCode.RUNTIME,
                           f'USRP 主时钟未生效：请求={requested:g} Hz，实际={actual:g} Hz。'
                           'N310 需要在创建设备时通过 master_clock_rate 初始化；请重新建立任务。')

    def get_master_clock_rate(self, mboard: int = 0) -> float:
        """获取 USRP 设备主板当前的主时钟频率 (MCR)。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")
        if hasattr(self.api.dll, "uhd_usrp_get_master_clock_rate"):
            rate = C.c_double(0.0)
            code = self.api.dll.uhd_usrp_get_master_clock_rate(self.handle, C.c_size_t(mboard), C.byref(rate))
            self.api.check(code, "uhd_usrp_get_master_clock_rate")
            suffix = '' if mboard == 0 else f'_mboard{mboard}'
            self.device_info['master_clock_rate' + suffix] = rate.value
            return rate.value
        return 0.0

    @control_locked
    def configure_rx(self, config: USRPConfig) -> float:
        """配置 RX 采样率、载频、增益、天线与模拟带宽，返回实际生效采样率。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")

        chan = C.c_size_t(config.channel)

        if config.master_clock_rate:
            self.set_master_clock_rate(config.master_clock_rate)

        if config.clock_source:
            code = self.api.dll.uhd_usrp_set_clock_source(self.handle, config.clock_source.encode("utf-8"), 0)
            self.api.check(code, "uhd_usrp_set_clock_source")

        if config.time_source:
            code = self.api.dll.uhd_usrp_set_time_source(self.handle, config.time_source.encode("utf-8"), 0)
            self.api.check(code, "uhd_usrp_set_time_source")

        # 采样率
        code = self.api.dll.uhd_usrp_set_rx_rate(self.handle, C.c_double(config.sample_rate), chan)
        self.api.check(code, "uhd_usrp_set_rx_rate")

        actual_rate = C.c_double(0.0)
        code = self.api.dll.uhd_usrp_get_rx_rate(self.handle, chan, C.byref(actual_rate))
        self.api.check(code, "uhd_usrp_get_rx_rate")
        rate_val = actual_rate.value

        if abs(rate_val - config.sample_rate) > 1.0:
            import warnings
            warnings.warn(
                f"USRP 硬件采样率被强制对齐(Coerced): 请求={config.sample_rate:g} Sps, "
                f"实际生效={rate_val:g} Sps (偏差 {rate_val - config.sample_rate:+g} Sps)。"
                f"当前实际 MCR={self.device_info.get('master_clock_rate', '未知')} Hz。",
                stacklevel=2,
            )

        # 载频
        tune_req = TuneRequest(
            target_freq=float(config.frequency_hz),
            rf_freq_policy=UHDTuneRequestPolicy.MANUAL if config.lo_frequency_hz else UHDTuneRequestPolicy.AUTO,
            rf_freq=config.lo_frequency_hz or 0.0,
            dsp_freq_policy=UHDTuneRequestPolicy.AUTO,
            dsp_freq=0.0,
            args=b"",
        )
        tune_res = TuneResult()
        code = self.api.dll.uhd_usrp_set_rx_freq(self.handle, C.byref(tune_req), chan, C.byref(tune_res))
        self.api.check(code, "uhd_usrp_set_rx_freq")

        # 增益
        code = self.api.dll.uhd_usrp_set_rx_gain(self.handle, C.c_double(config.gain), chan, b"")
        self.api.check(code, "uhd_usrp_set_rx_gain")

        # 模拟带宽
        if config.bandwidth_hz is not None:
            code = self.api.dll.uhd_usrp_set_rx_bandwidth(self.handle, C.c_double(config.bandwidth_hz), chan)
            self.api.check(code, "uhd_usrp_set_rx_bandwidth")

        # 天线端口
        if config.antenna:
            code = self.api.dll.uhd_usrp_set_rx_antenna(self.handle, config.antenna.encode("utf-8"), chan)
            self.api.check(code, "uhd_usrp_set_rx_antenna")

        return rate_val

    @control_locked
    def configure_tx(self, config: USRPConfig) -> float:
        """配置 TX 采样率、载频、增益、天线与模拟带宽，返回实际生效采样率。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")

        chan = C.c_size_t(config.channel)

        if config.master_clock_rate:
            self.set_master_clock_rate(config.master_clock_rate)

        if config.clock_source:
            code = self.api.dll.uhd_usrp_set_clock_source(self.handle, config.clock_source.encode("utf-8"), 0)
            self.api.check(code, "uhd_usrp_set_clock_source")

        if config.time_source:
            code = self.api.dll.uhd_usrp_set_time_source(self.handle, config.time_source.encode("utf-8"), 0)
            self.api.check(code, "uhd_usrp_set_time_source")

        # 采样率
        code = self.api.dll.uhd_usrp_set_tx_rate(self.handle, C.c_double(config.sample_rate), chan)
        self.api.check(code, "uhd_usrp_set_tx_rate")

        actual_rate = C.c_double(0.0)
        code = self.api.dll.uhd_usrp_get_tx_rate(self.handle, chan, C.byref(actual_rate))
        self.api.check(code, "uhd_usrp_get_tx_rate")
        rate_val = actual_rate.value

        if abs(rate_val - config.sample_rate) > 1.0:
            import warnings
            warnings.warn(
                f"USRP 硬件采样率被强制对齐(Coerced): 请求={config.sample_rate:g} Sps, "
                f"实际生效={rate_val:g} Sps (偏差 {rate_val - config.sample_rate:+g} Sps)。"
                f"当前实际 MCR={self.device_info.get('master_clock_rate', '未知')} Hz。",
                stacklevel=2,
            )

        # 载频
        tune_req = TuneRequest(
            target_freq=float(config.frequency_hz),
            rf_freq_policy=UHDTuneRequestPolicy.MANUAL if config.lo_frequency_hz else UHDTuneRequestPolicy.AUTO,
            rf_freq=config.lo_frequency_hz or 0.0,
            dsp_freq_policy=UHDTuneRequestPolicy.AUTO,
            dsp_freq=0.0,
            args=b"",
        )
        tune_res = TuneResult()
        code = self.api.dll.uhd_usrp_set_tx_freq(self.handle, C.byref(tune_req), chan, C.byref(tune_res))
        self.api.check(code, "uhd_usrp_set_tx_freq")

        # 增益
        code = self.api.dll.uhd_usrp_set_tx_gain(self.handle, C.c_double(config.gain), chan, b"")
        self.api.check(code, "uhd_usrp_set_tx_gain")

        # 模拟带宽
        if config.bandwidth_hz is not None:
            code = self.api.dll.uhd_usrp_set_tx_bandwidth(self.handle, C.c_double(config.bandwidth_hz), chan)
            self.api.check(code, "uhd_usrp_set_tx_bandwidth")

        # 天线端口
        if config.antenna:
            code = self.api.dll.uhd_usrp_set_tx_antenna(self.handle, config.antenna.encode("utf-8"), chan)
            self.api.check(code, "uhd_usrp_set_tx_antenna")

        return actual_rate.value

    def receive(
        self,
        sink,
        config: USRPConfig,
        sample_count: int,
        *,
        block_samples: int = 4096,
        timeout: float = 2.0,
        cancel_event=None,
        metadata_callback=None,
        rate_callback=None,
        duration_s: float | None = None,
        settle_seconds: float = 0.0,
        discard_seconds: float = 0.0,
    ) -> USRPStreamStats:
        """流式接收 IQ 采样并写入 sink（文件或回调函数）。"""
        if any(not isfinite(value) or value < 0 for value in (settle_seconds, discard_seconds)):
            raise ValueError("调谐稳定和丢弃时间必须为有限非负数")
        actual_rate = self.configure_rx(config)
        if duration_s is not None and duration_s > 0:
            sample_count = round(duration_s * actual_rate)
        if rate_callback is not None:
            rate_callback(actual_rate)
        if settle_seconds:
            # 可中断等待，停止扫描时不必等完稳定时间。
            if cancel_event is not None:
                cancel_event.wait(settle_seconds)
            else:
                time.sleep(settle_seconds)
        discard_samples = round(discard_seconds * actual_rate)
        capture_samples = sample_count + discard_samples if sample_count > 0 else sample_count
        if cancel_event is not None and cancel_event.is_set():
            return USRPStreamStats(mode="rx", actual_rate=actual_rate, device_info=self.device_info)

        chan = C.c_size_t(config.channel)
        stream_args = StreamArgs(
            cpu_format=b"fc32" if config.cpu_format == "sc8" else config.cpu_format.encode("ascii"),
            otw_format=config.otw_format.encode("ascii"),
            args=b"",
            channel_list=C.pointer(chan),
            n_channels=1,
        )

        streamer = C.c_void_p()
        code = self.api.dll.uhd_rx_streamer_make(C.byref(streamer))
        self.api.check(code, "uhd_rx_streamer_make")

        md = C.c_void_p()
        code = self.api.dll.uhd_rx_metadata_make(C.byref(md))
        self.api.check(code, "uhd_rx_metadata_make")

        bytes_per_sample = config.bytes_per_sample
        native_bytes_per_sample = 8 if config.cpu_format == "sc8" else bytes_per_sample
        raw_buffer = (C.c_uint8 * (block_samples * native_bytes_per_sample))()
        buf_ptr = C.cast(raw_buffer, C.c_void_p)
        buf_ptrs = (C.c_void_p * 1)(buf_ptr)

        stats = USRPStreamStats(mode="rx", actual_rate=actual_rate, device_info=self.device_info)
        start_time = time.monotonic()

        try:
            code = self.api.dll.uhd_usrp_get_rx_stream(self.handle, C.byref(stream_args), streamer)
            self.api.check(code, "uhd_usrp_get_rx_stream")

            is_continuous = (sample_count <= 0)
            cmd = StreamCmd(
                stream_mode=UHDStreamMode.START_CONTINUOUS if is_continuous else UHDStreamMode.NUM_SAMPS_AND_DONE,
                num_samps=0 if is_continuous else capture_samples,
                stream_now=True,
                time_spec_full_secs=0,
                time_spec_frac_secs=0.0,
            )
            code = self.api.dll.uhd_rx_streamer_issue_stream_cmd(streamer, C.byref(cmd))
            self.api.check(code, "uhd_rx_streamer_issue_stream_cmd")

            is_callable = callable(sink)
            err_code = C.c_int(0)
            items_recvd = C.c_size_t(0)
            first_hw_time = None

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if not is_continuous and stats.sample_count >= sample_count:
                    break

                to_recv = block_samples if is_continuous else min(block_samples, sample_count - stats.sample_count)
                recv_err = self.api.dll.uhd_rx_streamer_recv(
                    streamer,
                    buf_ptrs,
                    to_recv,
                    C.byref(md),
                    C.c_double(timeout),
                    False,
                    C.byref(items_recvd),
                )
                self.api.check(recv_err, "uhd_rx_streamer_recv")

                self.api.dll.uhd_rx_metadata_error_code(md, C.byref(err_code))
                if err_code.value == UHDRxMetadataErrorCode.OVERFLOW:
                    stats.overflow_count += 1
                elif err_code.value == UHDRxMetadataErrorCode.TIMEOUT:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    raise UHDError(UHDErrorCode.RUNTIME, "RX stream timed out")
                elif err_code.value != UHDRxMetadataErrorCode.NONE:
                    raise UHDError(UHDErrorCode.RUNTIME, f"RX metadata error 0x{err_code.value:x}")

                if err_code.value == UHDRxMetadataErrorCode.OVERFLOW:
                    items_recvd.value = 0

                has_ts = C.c_bool(False)
                full_s = C.c_int64(0)
                frac_s = C.c_double(0.0)
                self.api.dll.uhd_rx_metadata_has_time_spec(md, C.byref(has_ts))
                if has_ts.value:
                    self.api.dll.uhd_rx_metadata_time_spec(md, C.byref(full_s), C.byref(frac_s))
                    if first_hw_time is None and items_recvd.value:
                        first_hw_time = full_s.value + frac_s.value

                if metadata_callback is not None:
                    metadata_callback(
                        has_time=has_ts.value,
                        full_secs=full_s.value,
                        frac_secs=frac_s.value,
                        sample_count=items_recvd.value,
                        error_code=err_code.value,
                    )

                recvd = items_recvd.value
                if recvd > 0:
                    dropped = min(discard_samples, recvd)
                    discard_samples -= dropped
                    recvd -= dropped
                    chunk_bytes = recvd * bytes_per_sample
                    chunk = bytes(memoryview(raw_buffer)[dropped * native_bytes_per_sample:(dropped + recvd) * native_bytes_per_sample])
                    if config.cpu_format == "sc8":
                        # The Windows UHD build need not contain a sc16->sc8
                        # converter. Use the supported fc32 converter explicitly.
                        floats = np.frombuffer(chunk, dtype="<f4")
                        chunk = np.clip(np.rint(floats * 128), -128, 127).astype(np.int8).tobytes()
                    if is_callable:
                        sink(chunk)
                    else:
                        sink.write(chunk)
                    stats.sample_count += recvd
                    stats.byte_count += chunk_bytes
                end_of_burst = C.c_bool(False)
                self.api.check(self.api.dll.uhd_rx_metadata_end_of_burst(md, C.byref(end_of_burst)),
                               "uhd_rx_metadata_end_of_burst")
                if not is_continuous and end_of_burst.value:
                    break
                if (not is_continuous and has_ts.value and first_hw_time is not None
                        and full_s.value + frac_s.value + items_recvd.value / actual_rate
                        >= first_hw_time + (capture_samples - 0.5) / actual_rate):
                    # Finite UHD commands count elapsed radio samples, including
                    # samples lost in transport; don't wait forever to replace them.
                    break

        finally:
            # 停止连续流
            stop_cmd = StreamCmd(
                stream_mode=UHDStreamMode.STOP_CONTINUOUS,
                num_samps=0,
                stream_now=True,
                time_spec_full_secs=0,
                time_spec_frac_secs=0.0,
            )
            self.api.dll.uhd_rx_streamer_issue_stream_cmd(streamer, C.byref(stop_cmd))
            self.api.dll.uhd_rx_metadata_free(C.byref(md))
            self.api.dll.uhd_rx_streamer_free(C.byref(streamer))
            stats.elapsed_seconds = time.monotonic() - start_time
            self.last_stats = stats

        return stats

    def transmit(
        self,
        source,
        config: USRPConfig,
        *,
        block_samples: int = 4096,
        timeout: float = 2.0,
        cancel_event=None,
    ) -> USRPStreamStats:
        """从 source（二进制字节流或包含 read() 的文件对象）读取 IQ 并通过 TX streamer 发射。"""
        actual_rate = self.configure_tx(config)

        chan = C.c_size_t(config.channel)
        stream_args = StreamArgs(
            cpu_format=b"fc32" if config.cpu_format == "sc8" else config.cpu_format.encode("ascii"),
            otw_format=config.otw_format.encode("ascii"),
            args=b"",
            channel_list=C.pointer(chan),
            n_channels=1,
        )

        streamer = C.c_void_p()
        code = self.api.dll.uhd_tx_streamer_make(C.byref(streamer))
        self.api.check(code, "uhd_tx_streamer_make")

        md = C.c_void_p()
        code = self.api.dll.uhd_tx_metadata_make(C.byref(md), False, 0, 0.0, True, False)
        self.api.check(code, "uhd_tx_metadata_make")

        bytes_per_sample = config.bytes_per_sample
        native_bytes_per_sample = 8 if config.cpu_format == "sc8" else bytes_per_sample
        raw_buffer = (C.c_uint8 * (block_samples * native_bytes_per_sample))()
        buf_ptr = C.cast(raw_buffer, C.c_void_p)
        buf_ptrs = (C.c_void_p * 1)(buf_ptr)

        stats = USRPStreamStats(mode="tx", actual_rate=actual_rate, device_info=self.device_info)
        start_time = time.monotonic()

        try:
            code = self.api.dll.uhd_usrp_get_tx_stream(self.handle, C.byref(stream_args), streamer)
            self.api.check(code, "uhd_usrp_get_tx_stream")

            items_sent = C.c_size_t(0)

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    break

                read_size = block_samples * bytes_per_sample
                if hasattr(source, "read"):
                    data = source.read(read_size)
                else:
                    data = source[:read_size]
                    source = source[read_size:]

                if not data:
                    break

                samps_in_chunk = len(data) // bytes_per_sample
                if len(data) % bytes_per_sample:
                    raise ValueError("Incomplete IQ sample at TX input")
                if config.cpu_format == "sc8":
                    data = (np.frombuffer(data, dtype=np.int8).astype("<f4") / 128).tobytes()
                C.memmove(buf_ptr, data, samps_in_chunk * native_bytes_per_sample)

                send_err = self.api.dll.uhd_tx_streamer_send(
                    streamer,
                    buf_ptrs,
                    samps_in_chunk,
                    C.byref(md),
                    C.c_double(timeout),
                    C.byref(items_sent),
                )
                self.api.check(send_err, "uhd_tx_streamer_send")
                if items_sent.value != samps_in_chunk:
                    raise UHDError(UHDErrorCode.RUNTIME, f"Partial TX: {items_sent.value}/{samps_in_chunk} samples")

                stats.sample_count += items_sent.value
                stats.byte_count += items_sent.value * bytes_per_sample
                # Only the first buffer starts this continuous burst.
                self.api.dll.uhd_tx_metadata_free(C.byref(md))
                self.api.check(self.api.dll.uhd_tx_metadata_make(C.byref(md), False, 0, 0.0, False, False),
                               "uhd_tx_metadata_make")

            # 标记 End of Burst
            end_md = C.c_void_p()
            self.api.dll.uhd_tx_metadata_make(C.byref(end_md), False, 0, 0.0, False, True)
            self.api.dll.uhd_tx_streamer_send(
                streamer,
                buf_ptrs,
                0,
                C.byref(end_md),
                C.c_double(timeout),
                C.byref(items_sent),
            )
            self.api.dll.uhd_tx_metadata_free(C.byref(end_md))

        finally:
            self.api.dll.uhd_tx_metadata_free(C.byref(md))
            self.api.dll.uhd_tx_streamer_free(C.byref(streamer))
            stats.elapsed_seconds = time.monotonic() - start_time
            self.last_stats = stats

        return stats

    def prepare_transmit(self, config: USRPConfig) -> float:
        """Configure before choosing a deadline; reuse the TX streamer."""
        with self._tx_lock, self.command_lock:
            if self._burst_config == config:
                return self._burst_rate
            if (self._burst_config is not None and config.lo_frequency_hz
                    and {k:v for k,v in asdict(self._burst_config).items() if k != "frequency_hz"}
                    == {k:v for k,v in asdict(config).items() if k != "frequency_hz"}):
                request = TuneRequest(float(config.frequency_hz), UHDTuneRequestPolicy.NONE, 0.0,
                                      UHDTuneRequestPolicy.AUTO, 0.0, b"")
                result = TuneResult()
                self.api.check(self.api.dll.uhd_usrp_set_tx_freq(
                    self.handle, C.byref(request), C.c_size_t(config.channel), C.byref(result)),
                    "set_tx_freq (digital)")
                self._burst_config = config
                return self._burst_rate
            actual_rate = self.configure_tx(config)
            signature = lambda c: (c.channel, c.cpu_format, c.otw_format)
            if self._burst_config is not None and signature(self._burst_config) != signature(config):
                self.api.check(self.api.dll.uhd_tx_streamer_free(C.byref(self._burst_stream)), "uhd_tx_streamer_free")
            if not self._burst_stream.value:
                channel = C.c_size_t(config.channel)
                native_cpu = b"fc32" if config.cpu_format == "sc8" else config.cpu_format.encode()
                args = StreamArgs(native_cpu, config.otw_format.encode(), b"", C.pointer(channel), 1)
                self.api.check(self.api.dll.uhd_tx_streamer_make(C.byref(self._burst_stream)), "uhd_tx_streamer_make")
                try:
                    self.api.check(self.api.dll.uhd_usrp_get_tx_stream(self.handle, C.byref(args), self._burst_stream),
                                   "uhd_usrp_get_tx_stream")
                except BaseException:
                    self.api.dll.uhd_tx_streamer_free(C.byref(self._burst_stream))
                    raise
            self._burst_config, self._burst_rate = config, actual_rate
            return actual_rate

    def _wait_burst_ack(self, streamer, timeout: float):
        md = C.c_void_p()
        self.api.check(self.api.dll.uhd_async_metadata_make(C.byref(md)), "uhd_async_metadata_make")
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                valid, event = C.c_bool(), C.c_int()
                self.api.check(self.api.dll.uhd_tx_streamer_recv_async_msg(
                    streamer, C.byref(md), C.c_double(min(0.1, max(0.0, deadline - time.monotonic()))),
                    C.byref(valid)), "uhd_tx_streamer_recv_async_msg")
                if not valid.value:
                    continue
                self.api.check(self.api.dll.uhd_async_metadata_event_code(md, C.byref(event)),
                               "uhd_async_metadata_event_code")
                errors = event.value & ~int(UHDAsyncMetadataEventCode.BURST_ACK)
                if errors:
                    raise UHDError(UHDErrorCode.RUNTIME, f"TX async error 0x{event.value:x} (late/underflow/sequence)")
                if event.value & UHDAsyncMetadataEventCode.BURST_ACK:
                    return
            raise UHDError(UHDErrorCode.RUNTIME, "Timed TX completion BURST_ACK timeout")
        finally:
            self.api.dll.uhd_async_metadata_free(C.byref(md))

    @tx_locked
    def transmit_burst(
        self,
        samples: bytes,
        config: USRPConfig,
        target_hw_time_s: float | None = None,
        *,
        timeout: float = 2.0,
    ) -> int:
        """通过 TX streamer 发射单次短突发，支持精确硬件时间戳定时发射 (Timed Burst)。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")

        if not samples or len(samples) % config.bytes_per_sample:
            raise ValueError("Burst must contain complete, nonempty IQ samples")
        actual_rate = self.prepare_transmit(config)
        now = self.get_time_estimate()
        if target_hw_time_s is not None and target_hw_time_s <= now + 0.001:
            raise UHDError(UHDErrorCode.RUNTIME, "Timed TX deadline is past or too close")
        streamer = self._burst_stream

        has_time_spec = (target_hw_time_s is not None)
        full_secs = int(target_hw_time_s) if target_hw_time_s is not None else 0
        frac_secs = float(target_hw_time_s - full_secs) if target_hw_time_s is not None else 0.0

        md = C.c_void_p()
        code = self.api.dll.uhd_tx_metadata_make(
            C.byref(md),
            C.c_bool(has_time_spec),
            C.c_int64(full_secs),
            C.c_double(frac_secs),
            C.c_bool(True),   # start_of_burst
            C.c_bool(True),   # end_of_burst
        )
        self.api.check(code, "uhd_tx_metadata_make")

        bytes_per_sample = config.bytes_per_sample
        sample_count = len(samples) // bytes_per_sample

        native = ((np.frombuffer(samples, dtype=np.int8).astype("<f4") / 128).tobytes()
                  if config.cpu_format == "sc8" else samples)
        raw_buffer = (C.c_uint8 * len(native)).from_buffer_copy(native)
        buf_ptr = C.cast(raw_buffer, C.c_void_p)
        buf_ptrs = (C.c_void_p * 1)(buf_ptr)

        items_sent = C.c_size_t(0)
        try:
            send_err = self.api.dll.uhd_tx_streamer_send(
                streamer,
                buf_ptrs,
                sample_count,
                C.byref(md),
                C.c_double(timeout),
                C.byref(items_sent),
            )
            self.api.check(send_err, "uhd_tx_streamer_send")
            if items_sent.value != sample_count:
                raise UHDError(UHDErrorCode.RUNTIME, f"Partial burst: {items_sent.value}/{sample_count} samples")
            self._wait_burst_ack(streamer, timeout + max(0.0, (target_hw_time_s or now) - now)
                                 + sample_count / actual_rate)
            return items_sent.value
        except BaseException:
            # A failed burst can leave async events pending. A new streamer is
            # required before retrying, so an old ACK cannot confirm a new TX.
            self.api.dll.uhd_tx_streamer_free(C.byref(self._burst_stream))
            self._burst_config = None
            raise
        finally:
            self.api.dll.uhd_tx_metadata_free(C.byref(md))
