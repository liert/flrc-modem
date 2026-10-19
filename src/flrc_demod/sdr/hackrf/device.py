"""设备句柄生命周期、参数配置及流式操作互斥。"""
import ctypes as C
import threading

from .config import HackRFConfig
from .abi import M0State
from .library import HackRFError, HackRFLibrary


class HackRFDevice:
    def __init__(self, library: HackRFLibrary | None = None, serial: str | None = None):
        self.api = library or HackRFLibrary()
        self.serial = serial
        self.handle = C.c_void_p()
        self._lock = threading.Lock()
        self._stream = None
        self.last_stats = None

    def __enter__(self):
        if self.handle.value:
            raise HackRFError("设备已经打开，不能重复进入上下文")
        self.api.acquire()
        try:
            if self.serial is None:
                self.api.check(self.api.hackrf_open(C.byref(self.handle)), "hackrf_open")
            else:
                self.api.check(self.api.hackrf_open_by_serial(self.serial.encode("ascii"), C.byref(self.handle)), "hackrf_open_by_serial")
        except BaseException:
            self.api.release()
            raise
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            self.close()
        except BaseException as error:
            if exception is None:
                raise
            exception.add_note(f"关闭设备时另有错误：{error}")

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise HackRFError("流式操作正在运行，请先取消或等待结束")
        try:
            if self.handle.value:
                # libhackrf 即使返回 stop_cmd 错误也已释放句柄，不能再次 close。
                code = self.api.hackrf_close(self.handle)
                self.handle = C.c_void_p()
                self._stream = None
                try:
                    self.api.check(code, "hackrf_close")
                finally:
                    self.api.release()
        finally:
            self._lock.release()

    def _configure(self, config: HackRFConfig, tx: bool):
        if not self.handle.value:
            raise HackRFError("设备未打开，请在 with HackRFDevice(...) 中使用")
        calls = [("hackrf_set_sample_rate", config.sample_rate),
                 ("hackrf_set_baseband_filter_bandwidth", config.bandwidth_hz),
                 ("hackrf_set_freq", config.frequency_hz),
                 ("hackrf_set_amp_enable", int(config.amp)),
                 ("hackrf_set_antenna_enable", int(config.bias_tee))]
        calls += [("hackrf_set_txvga_gain", config.tx_gain)] if tx else [
            ("hackrf_set_lna_gain", config.lna_gain), ("hackrf_set_vga_gain", config.vga_gain)]
        for operation, value in calls:
            self.api.check(getattr(self.api, operation)(self.handle, value), operation)

    def info(self):
        if not self.handle.value:
            raise HackRFError("设备未打开")
        firmware, usb_api = C.create_string_buffer(256), C.c_uint16()
        self.api.check(self.api.hackrf_version_string_read(self.handle, firmware, 255), "hackrf_version_string_read")
        self.api.check(self.api.hackrf_usb_api_version_read(self.handle, C.byref(usb_api)), "hackrf_usb_api_version_read")
        return {"firmware": firmware.value.decode(errors="replace"), "usb_api": f"0x{usb_api.value:04x}",
                "native_buffer_bytes": int(self.api.hackrf_get_transfer_buffer_size(self.handle)),
                "native_queue_depth": int(self.api.hackrf_get_transfer_queue_depth(self.handle))}

    def state(self):
        if not self.handle.value:
            raise HackRFError("设备未打开")
        state = M0State()
        self.api.check(self.api.hackrf_get_m0_state(self.handle, C.byref(state)), "hackrf_get_m0_state")
        return {name: getattr(state, name) for name, _ in state._fields_}

    def transmit(self, source, config: HackRFConfig, **options):
        """source 实现 readinto(memoryview)，内容为交错 cs8；不取得 source 所有权。"""
        from .streaming import TxStream
        return self._run(source, config, TxStream, True, options)

    def receive(self, sink, config: HackRFConfig, sample_count: int | None = None, **options):
        """sink 实现 write(buffer)，接收原始 cs8；写文件在工作线程进行。"""
        from .streaming import RxStream
        return self._run(sink, config, RxStream, False, {"sample_count": sample_count, **options})

    def _run(self, io_object, config, stream_class, tx, options):
        allow_device_shortfalls = options.pop("allow_device_shortfalls", False)
        if not self._lock.acquire(blocking=False):
            raise HackRFError("同一设备不能同时运行多个流式操作")
        try:
            # 保留失败停止时的回调对象，直到 close 确认停止底层线程。
            if self._stream is not None:
                raise HackRFError("上一次流未成功停止，请关闭并重新打开设备")
            self.last_stats = None
            self._configure(config, tx)
            stream = stream_class(self.api, self.handle, io_object, **options)
            self._stream = stream
            try:
                result = stream.run()
            finally:
                self.last_stats = stream.stats
                if stream.stopped:
                    # 出错时也保留部分传输和固件统计，供上层诊断。
                    try:
                        stream.stats.device_state = self.state()
                    except (HackRFError, AttributeError) as error:
                        stream.stats.device_state_error = str(error)
                    self._stream = None
            state = result.device_state
            if state is not None:
                if state["error"]:
                    raise HackRFError(f"设备固件流式错误：{state['error']}")
                if state["num_shortfalls"] and not allow_device_shortfalls:
                    from .streaming import StreamOverrun, StreamUnderrun
                    error_class = StreamUnderrun if tx else StreamOverrun
                    raise error_class(f"设备固件记录 {state['num_shortfalls']} 次{'欠载' if tx else '溢出'}，"
                                      f"最长 {state['longest_shortfall']} 字节；数据连续性不能保证")
            return result
        finally:
            self._lock.release()
