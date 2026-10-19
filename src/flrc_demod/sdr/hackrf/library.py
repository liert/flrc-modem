"""DLL 路径、依赖搜索、函数签名和进程内 libhackrf 初始化计数。"""
import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path
import threading

from .abi import CompleteCallback, DeviceList, FlushCallback, M0State, SampleCallback

_context_lock = threading.Lock()
_context_refs: dict[str, int] = {}


class HackRFError(RuntimeError):
    pass


def bundled_dll_path() -> Path:
    return Path(__file__).parent / "native" / "windows-x64" / "libhackrf.dll"


class HackRFLibrary:
    def __init__(self, dll_path: str | Path | None = None):
        if dll_path is None:
            dll_path = bundled_dll_path() if os.name == "nt" else find_library("hackrf")
        if not dll_path:
            raise HackRFError("找不到 libhackrf，请显式指定库路径")
        self.path = str(Path(dll_path).resolve()) if Path(dll_path).exists() else str(dll_path)
        self._key = os.path.normcase(self.path)
        self._dependency_directory = None
        if os.name == "nt":
            if not Path(self.path).is_file():
                raise HackRFError(f"DLL 不存在：{self.path}")
            if self.path == str(bundled_dll_path().resolve()) and C.sizeof(C.c_void_p) != 8:
                raise HackRFError("项目内 DLL 为 x64，需要 64 位 Python")
            # 句柄必须跟库对象一起保留，不能离开 with 块后关闭依赖搜索路径。
            self._dependency_directory = os.add_dll_directory(str(Path(self.path).parent))
        try:
            # 官方 Windows 头文件规定 __cdecl，不能使用 WinDLL/stdcall。
            self.dll = C.CDLL(self.path)
            # 同一个 native handle 即使经不同路径加载，也共享初始化计数。
            self._key = str(self.dll._handle)
            self._bind_api()
        except (OSError, AttributeError) as error:
            if self._dependency_directory is not None:
                self._dependency_directory.close()
            raise HackRFError(f"加载 libhackrf 失败：{self.path}；{error}") from error

    def bind(self, name: str, restype, argtypes):
        """高级扩展入口：显式声明 ABI 后调用其他 DLL 导出函数。"""
        function = getattr(self.dll, name)
        function.restype, function.argtypes = restype, argtypes
        return function

    def _bind_api(self):
        ptr = C.c_void_p
        signatures = {
            "hackrf_init": (C.c_int, []), "hackrf_exit": (C.c_int, []),
            "hackrf_library_version": (C.c_char_p, []),
            "hackrf_library_release": (C.c_char_p, []),
            "hackrf_error_name": (C.c_char_p, [C.c_int]),
            "hackrf_device_list": (C.POINTER(DeviceList), []),
            "hackrf_device_list_free": (None, [C.POINTER(DeviceList)]),
            "hackrf_open": (C.c_int, [C.POINTER(ptr)]),
            "hackrf_open_by_serial": (C.c_int, [C.c_char_p, C.POINTER(ptr)]),
            "hackrf_close": (C.c_int, [ptr]),
            "hackrf_set_freq": (C.c_int, [ptr, C.c_uint64]),
            "hackrf_set_sample_rate": (C.c_int, [ptr, C.c_double]),
            "hackrf_set_baseband_filter_bandwidth": (C.c_int, [ptr, C.c_uint32]),
            "hackrf_set_txvga_gain": (C.c_int, [ptr, C.c_uint32]),
            "hackrf_set_lna_gain": (C.c_int, [ptr, C.c_uint32]),
            "hackrf_set_vga_gain": (C.c_int, [ptr, C.c_uint32]),
            "hackrf_set_amp_enable": (C.c_int, [ptr, C.c_uint8]),
            "hackrf_set_antenna_enable": (C.c_int, [ptr, C.c_uint8]),
            "hackrf_start_tx": (C.c_int, [ptr, SampleCallback, ptr]),
            "hackrf_stop_tx": (C.c_int, [ptr]),
            "hackrf_start_rx": (C.c_int, [ptr, SampleCallback, ptr]),
            "hackrf_stop_rx": (C.c_int, [ptr]),
            "hackrf_is_streaming": (C.c_int, [ptr]),
            "hackrf_enable_tx_flush": (C.c_int, [ptr, FlushCallback, ptr]),
            "hackrf_disable_tx_flush": (C.c_int, [ptr]),
            "hackrf_set_tx_block_complete_callback": (C.c_int, [ptr, CompleteCallback]),
            "hackrf_get_transfer_buffer_size": (C.c_size_t, [ptr]),
            "hackrf_get_transfer_queue_depth": (C.c_uint32, [ptr]),
            "hackrf_usb_api_version_read": (C.c_int, [ptr, C.POINTER(C.c_uint16)]),
            "hackrf_version_string_read": (C.c_int, [ptr, C.c_char_p, C.c_uint8]),
            "hackrf_get_m0_state": (C.c_int, [ptr, C.POINTER(M0State)]),
        }
        for name, (result, arguments) in signatures.items():
            setattr(self, name, self.bind(name, result, arguments))

    def check(self, code: int, operation: str):
        if code != 0:
            name = self.hackrf_error_name(code)
            description = name.decode("utf-8", errors="replace") if name else "unknown"
            raise HackRFError(f"{operation}：{description} ({code})")

    def acquire(self):
        with _context_lock:
            count = _context_refs.get(self._key, 0)
            if not count:
                self.check(self.hackrf_init(), "hackrf_init")
            _context_refs[self._key] = count + 1

    def release(self):
        with _context_lock:
            count = _context_refs[self._key]
            if count == 1:
                self.check(self.hackrf_exit(), "hackrf_exit")
                del _context_refs[self._key]
            else:
                _context_refs[self._key] = count - 1

    def info(self) -> dict:
        """只枚举 USB 和读取库版本，不打开射频流。"""
        self.acquire()
        listing = None
        try:
            listing = self.hackrf_device_list()
            if not listing:
                raise HackRFError("hackrf_device_list 返回空指针")
            serials = [listing.contents.serial_numbers[index] for index in range(listing.contents.devicecount)]
            return {"dll": self.path,
                    "library_version": self.hackrf_library_version().decode(errors="replace"),
                    "library_release": self.hackrf_library_release().decode(errors="replace"),
                    "serial_numbers": [serial.decode(errors="replace") if serial else None for serial in serials]}
        finally:
            if listing:
                self.hackrf_device_list_free(listing)
            self.release()
