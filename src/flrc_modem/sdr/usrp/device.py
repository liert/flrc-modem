"""USRP 硬件句柄上下文管理、射频配置与收发流式传输。"""
import ctypes as C
from dataclasses import asdict, dataclass
import threading
import time

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
from .config import USRPConfig
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

    def __init__(self, library: UHDLibrary | None = None, device_args: str = ""):
        self.api = library or UHDLibrary()
        self.device_args = device_args.strip()
        self.handle = C.c_void_p()
        self._lock = threading.Lock()
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
            self._query_info()

    def close(self):
        """释放底层 USRP 硬件句柄。"""
        with self._lock:
            if self.handle.value:
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

    def configure_rx(self, config: USRPConfig) -> float:
        """配置 RX 采样率、载频、增益、天线与模拟带宽，返回实际生效采样率。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")

        chan = C.c_size_t(config.channel)

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

        # 载频
        tune_req = TuneRequest(
            target_freq=float(config.frequency_hz),
            rf_freq_policy=UHDTuneRequestPolicy.AUTO,
            rf_freq=0.0,
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

        return actual_rate.value

    def configure_tx(self, config: USRPConfig) -> float:
        """配置 TX 采样率、载频、增益、天线与模拟带宽，返回实际生效采样率。"""
        if not self.handle.value:
            raise UHDError(UHDErrorCode.INVALID_DEVICE, "设备未打开")

        chan = C.c_size_t(config.channel)

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

        # 载频
        tune_req = TuneRequest(
            target_freq=float(config.frequency_hz),
            rf_freq_policy=UHDTuneRequestPolicy.AUTO,
            rf_freq=0.0,
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
    ) -> USRPStreamStats:
        """流式接收 IQ 采样并写入 sink（文件或回调函数）。"""
        actual_rate = self.configure_rx(config)

        chan = C.c_size_t(config.channel)
        stream_args = StreamArgs(
            cpu_format=config.cpu_format.encode("ascii"),
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
        raw_buffer = (C.c_uint8 * (block_samples * bytes_per_sample))()
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
                num_samps=0 if is_continuous else sample_count,
                stream_now=True,
                time_spec_full_secs=0,
                time_spec_frac_secs=0.0,
            )
            code = self.api.dll.uhd_rx_streamer_issue_stream_cmd(streamer, C.byref(cmd))
            self.api.check(code, "uhd_rx_streamer_issue_stream_cmd")

            is_callable = callable(sink)
            err_code = C.c_int(0)
            items_recvd = C.c_size_t(0)

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

                has_ts = C.c_bool(False)
                full_s = C.c_int64(0)
                frac_s = C.c_double(0.0)
                self.api.dll.uhd_rx_metadata_has_time_spec(md, C.byref(has_ts))
                if has_ts.value:
                    self.api.dll.uhd_rx_metadata_time_spec(md, C.byref(full_s), C.byref(frac_s))

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
                    chunk_bytes = recvd * bytes_per_sample
                    chunk = bytes(memoryview(raw_buffer)[:chunk_bytes])
                    if is_callable:
                        sink(chunk)
                    else:
                        sink.write(chunk)
                    stats.sample_count += recvd
                    stats.byte_count += chunk_bytes

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
            cpu_format=config.cpu_format.encode("ascii"),
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
        raw_buffer = (C.c_uint8 * (block_samples * bytes_per_sample))()
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
                C.memmove(buf_ptr, data, samps_in_chunk * bytes_per_sample)

                send_err = self.api.dll.uhd_tx_streamer_send(
                    streamer,
                    buf_ptrs,
                    samps_in_chunk,
                    C.byref(md),
                    C.c_double(timeout),
                    C.byref(items_sent),
                )
                self.api.check(send_err, "uhd_tx_streamer_send")

                stats.sample_count += items_sent.value
                stats.byte_count += items_sent.value * bytes_per_sample

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
