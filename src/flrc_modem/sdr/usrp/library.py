"""UHD C 动态链接库加载、ABI 函数绑定与错误检查。"""
import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path

from .abi import (
    StreamArgs,
    StreamCmd,
    TuneRequest,
    TuneResult,
    UHDErrorCode,
    UHDRxMetadataErrorCode,
)


class UHDError(RuntimeError):
    """UHD C API 调用失败异常。"""
    def __init__(self, code: int, message: str, function: str = ""):
        self.code = code
        self.raw_message = message
        self.function = function
        prefix = f"{function}: " if function else ""
        super().__init__(f"{prefix}[错误码 {code}] {message}")


def default_dll_search_paths() -> list[Path]:
    """返回系统内潜在的 UHD DLL 搜索路径。"""
    candidates = []
    if os.name == "nt":
        candidates.append(Path(r"C:\Program Files\UHD\bin\uhd.dll"))
        candidates.append(Path(r"C:\msys64\ucrt64\bin\uhd.dll"))
    uhd_dir = os.environ.get("UHD_DIR")
    if uhd_dir:
        candidates.insert(0, Path(uhd_dir) / "bin" / "uhd.dll")
    return candidates


def find_uhd_dll(explicit_path: str | Path | None = None) -> Path:
    """定位有效的 uhd.dll 文件路径。"""
    if explicit_path is not None:
        p = Path(explicit_path).resolve()
        if not p.is_file():
            raise UHDError(UHDErrorCode.NOT_IMPLEMENTED, f"指定的 UHD DLL 不存在：{p}")
        return p

    for candidate in default_dll_search_paths():
        if candidate.is_file():
            return candidate

    found = find_library("uhd")
    if found:
        return Path(found).resolve()

    raise UHDError(
        UHDErrorCode.NOT_IMPLEMENTED,
        "未在系统中找到 uhd.dll，请安装 UHD 或通过 --dll 显式传入库文件路径",
    )


class UHDLibrary:
    """封装 UHD C API 的动态链接库加载器。"""

    def __init__(self, dll_path: str | Path | None = None):
        self.path = find_uhd_dll(dll_path)
        self._dll_dir_cookie = None
        if os.name == "nt":
            self._dll_dir_cookie = os.add_dll_directory(str(self.path.parent))
        try:
            self.dll = C.CDLL(str(self.path))
            self._bind_api()
        except Exception as err:
            if self._dll_dir_cookie is not None:
                self._dll_dir_cookie.close()
            raise UHDError(UHDErrorCode.RUNTIME, f"加载 UHD 动态库失败：{self.path}；{err}") from err

    def check(self, code: int, function_name: str = ""):
        """检查 C API 返回码，若非零则提取错误消息并抛出 UHDError。"""
        if code == UHDErrorCode.NONE:
            return
        buf = C.create_string_buffer(1024)
        if hasattr(self.dll, "uhd_get_last_error"):
            self.dll.uhd_get_last_error(buf, 1024)
            msg = buf.value.decode("utf-8", errors="replace")
        else:
            msg = f"API 返回非零错误码 {code}"
        raise UHDError(code, msg, function_name)

    def get_version(self) -> str:
        """获取 UHD 版本字符串。"""
        buf = C.create_string_buffer(256)
        code = self.dll.uhd_get_version_string(buf, 256)
        self.check(code, "uhd_get_version_string")
        return buf.value.decode("utf-8", errors="replace")

    def info(self, args: str = "") -> dict:
        """返回本机 UHD 版本与探测到的所有 USRP 设备字典。"""
        from .discovery import discover_usrp_devices

        version = self.get_version()
        devices = discover_usrp_devices(self, args=args)
        return {
            "library_path": str(self.path),
            "library_version": version,
            "devices": devices,
        }

    def _bind_api(self):
        """声明所有使用的 C API 函数原型。"""
        ptr = C.c_void_p
        size_t = C.c_size_t

        # 基础信息与错误
        self.dll.uhd_get_version_string.restype = C.c_int
        self.dll.uhd_get_version_string.argtypes = [C.c_char_p, size_t]

        self.dll.uhd_get_last_error.restype = C.c_int
        self.dll.uhd_get_last_error.argtypes = [C.c_char_p, size_t]

        # 字符串向量 (uhd_string_vector)
        self.dll.uhd_string_vector_make.restype = C.c_int
        self.dll.uhd_string_vector_make.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_string_vector_free.restype = C.c_int
        self.dll.uhd_string_vector_free.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_string_vector_size.restype = C.c_int
        self.dll.uhd_string_vector_size.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_string_vector_at.restype = C.c_int
        self.dll.uhd_string_vector_at.argtypes = [ptr, size_t, C.c_char_p, size_t]

        # 设备发现与创建
        self.dll.uhd_usrp_find.restype = C.c_int
        self.dll.uhd_usrp_find.argtypes = [C.c_char_p, C.POINTER(ptr)]

        self.dll.uhd_usrp_make.restype = C.c_int
        self.dll.uhd_usrp_make.argtypes = [C.POINTER(ptr), C.c_char_p]

        self.dll.uhd_usrp_free.restype = C.c_int
        self.dll.uhd_usrp_free.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_usrp_last_error.restype = C.c_int
        self.dll.uhd_usrp_last_error.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_pp_string.restype = C.c_int
        self.dll.uhd_usrp_get_pp_string.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_mboard_name.restype = C.c_int
        self.dll.uhd_usrp_get_mboard_name.argtypes = [ptr, size_t, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_rx_num_channels.restype = C.c_int
        self.dll.uhd_usrp_get_rx_num_channels.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_usrp_get_tx_num_channels.restype = C.c_int
        self.dll.uhd_usrp_get_tx_num_channels.argtypes = [ptr, C.POINTER(size_t)]

        # 射频参数设置与获取 (RX)
        self.dll.uhd_usrp_set_rx_rate.restype = C.c_int
        self.dll.uhd_usrp_set_rx_rate.argtypes = [ptr, C.c_double, size_t]

        self.dll.uhd_usrp_get_rx_rate.restype = C.c_int
        self.dll.uhd_usrp_get_rx_rate.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_rx_freq.restype = C.c_int
        self.dll.uhd_usrp_set_rx_freq.argtypes = [ptr, C.POINTER(TuneRequest), size_t, C.POINTER(TuneResult)]

        self.dll.uhd_usrp_get_rx_freq.restype = C.c_int
        self.dll.uhd_usrp_get_rx_freq.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_rx_gain.restype = C.c_int
        self.dll.uhd_usrp_set_rx_gain.argtypes = [ptr, C.c_double, size_t, C.c_char_p]

        self.dll.uhd_usrp_get_rx_gain.restype = C.c_int
        self.dll.uhd_usrp_get_rx_gain.argtypes = [ptr, size_t, C.c_char_p, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_rx_bandwidth.restype = C.c_int
        self.dll.uhd_usrp_set_rx_bandwidth.argtypes = [ptr, C.c_double, size_t]

        self.dll.uhd_usrp_get_rx_bandwidth.restype = C.c_int
        self.dll.uhd_usrp_get_rx_bandwidth.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_rx_antenna.restype = C.c_int
        self.dll.uhd_usrp_set_rx_antenna.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_rx_antenna.restype = C.c_int
        self.dll.uhd_usrp_get_rx_antenna.argtypes = [ptr, size_t, C.c_char_p, size_t]

        # 射频参数设置与获取 (TX)
        self.dll.uhd_usrp_set_tx_rate.restype = C.c_int
        self.dll.uhd_usrp_set_tx_rate.argtypes = [ptr, C.c_double, size_t]

        self.dll.uhd_usrp_get_tx_rate.restype = C.c_int
        self.dll.uhd_usrp_get_tx_rate.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_tx_freq.restype = C.c_int
        self.dll.uhd_usrp_set_tx_freq.argtypes = [ptr, C.POINTER(TuneRequest), size_t, C.POINTER(TuneResult)]

        self.dll.uhd_usrp_get_tx_freq.restype = C.c_int
        self.dll.uhd_usrp_get_tx_freq.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_tx_gain.restype = C.c_int
        self.dll.uhd_usrp_set_tx_gain.argtypes = [ptr, C.c_double, size_t, C.c_char_p]

        self.dll.uhd_usrp_get_tx_gain.restype = C.c_int
        self.dll.uhd_usrp_get_tx_gain.argtypes = [ptr, size_t, C.c_char_p, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_tx_bandwidth.restype = C.c_int
        self.dll.uhd_usrp_set_tx_bandwidth.argtypes = [ptr, C.c_double, size_t]

        self.dll.uhd_usrp_get_tx_bandwidth.restype = C.c_int
        self.dll.uhd_usrp_get_tx_bandwidth.argtypes = [ptr, size_t, C.POINTER(C.c_double)]

        self.dll.uhd_usrp_set_tx_antenna.restype = C.c_int
        self.dll.uhd_usrp_set_tx_antenna.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_tx_antenna.restype = C.c_int
        self.dll.uhd_usrp_get_tx_antenna.argtypes = [ptr, size_t, C.c_char_p, size_t]

        # 时钟源与时间同步
        self.dll.uhd_usrp_set_clock_source.restype = C.c_int
        self.dll.uhd_usrp_set_clock_source.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_clock_source.restype = C.c_int
        self.dll.uhd_usrp_get_clock_source.argtypes = [ptr, size_t, C.c_char_p, size_t]

        self.dll.uhd_usrp_set_time_source.restype = C.c_int
        self.dll.uhd_usrp_set_time_source.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_usrp_get_time_source.restype = C.c_int
        self.dll.uhd_usrp_get_time_source.argtypes = [ptr, size_t, C.c_char_p, size_t]

        self.dll.uhd_usrp_set_time_now.restype = C.c_int
        self.dll.uhd_usrp_set_time_now.argtypes = [ptr, C.c_int64, C.c_double, size_t]

        self.dll.uhd_usrp_get_time_now.restype = C.c_int
        self.dll.uhd_usrp_get_time_now.argtypes = [ptr, size_t, C.POINTER(C.c_int64), C.POINTER(C.c_double)]

        # Timed Commands (硬件定时命令)
        self.dll.uhd_usrp_set_command_time.restype = C.c_int
        self.dll.uhd_usrp_set_command_time.argtypes = [ptr, C.c_int64, C.c_double, size_t]

        self.dll.uhd_usrp_clear_command_time.restype = C.c_int
        self.dll.uhd_usrp_clear_command_time.argtypes = [ptr, size_t]

        # Streamer: RX
        self.dll.uhd_rx_streamer_make.restype = C.c_int
        self.dll.uhd_rx_streamer_make.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_rx_streamer_free.restype = C.c_int
        self.dll.uhd_rx_streamer_free.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_rx_streamer_num_channels.restype = C.c_int
        self.dll.uhd_rx_streamer_num_channels.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_rx_streamer_max_num_samps.restype = C.c_int
        self.dll.uhd_rx_streamer_max_num_samps.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_usrp_get_rx_stream.restype = C.c_int
        self.dll.uhd_usrp_get_rx_stream.argtypes = [ptr, C.POINTER(StreamArgs), ptr]

        self.dll.uhd_rx_streamer_issue_stream_cmd.restype = C.c_int
        self.dll.uhd_rx_streamer_issue_stream_cmd.argtypes = [ptr, C.POINTER(StreamCmd)]

        self.dll.uhd_rx_streamer_recv.restype = C.c_int
        self.dll.uhd_rx_streamer_recv.argtypes = [ptr, C.POINTER(ptr), size_t, C.POINTER(ptr), C.c_double, C.c_bool, C.POINTER(size_t)]

        # Streamer: TX
        self.dll.uhd_tx_streamer_make.restype = C.c_int
        self.dll.uhd_tx_streamer_make.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_tx_streamer_free.restype = C.c_int
        self.dll.uhd_tx_streamer_free.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_tx_streamer_num_channels.restype = C.c_int
        self.dll.uhd_tx_streamer_num_channels.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_tx_streamer_max_num_samps.restype = C.c_int
        self.dll.uhd_tx_streamer_max_num_samps.argtypes = [ptr, C.POINTER(size_t)]

        self.dll.uhd_usrp_get_tx_stream.restype = C.c_int
        self.dll.uhd_usrp_get_tx_stream.argtypes = [ptr, C.POINTER(StreamArgs), ptr]

        self.dll.uhd_tx_streamer_send.restype = C.c_int
        self.dll.uhd_tx_streamer_send.argtypes = [ptr, C.POINTER(ptr), size_t, C.POINTER(ptr), C.c_double, C.POINTER(size_t)]

        # Metadata: RX
        self.dll.uhd_rx_metadata_make.restype = C.c_int
        self.dll.uhd_rx_metadata_make.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_rx_metadata_free.restype = C.c_int
        self.dll.uhd_rx_metadata_free.argtypes = [C.POINTER(ptr)]

        self.dll.uhd_rx_metadata_error_code.restype = C.c_int
        self.dll.uhd_rx_metadata_error_code.argtypes = [ptr, C.POINTER(C.c_int)]

        self.dll.uhd_rx_metadata_strerror.restype = C.c_int
        self.dll.uhd_rx_metadata_strerror.argtypes = [ptr, C.c_char_p, size_t]

        self.dll.uhd_rx_metadata_time_spec.restype = C.c_int
        self.dll.uhd_rx_metadata_time_spec.argtypes = [ptr, C.POINTER(C.c_int64), C.POINTER(C.c_double)]

        self.dll.uhd_rx_metadata_has_time_spec.restype = C.c_int
        self.dll.uhd_rx_metadata_has_time_spec.argtypes = [ptr, C.POINTER(C.c_bool)]

        self.dll.uhd_rx_metadata_start_of_burst.restype = C.c_int
        self.dll.uhd_rx_metadata_start_of_burst.argtypes = [ptr, C.POINTER(C.c_bool)]

        self.dll.uhd_rx_metadata_end_of_burst.restype = C.c_int
        self.dll.uhd_rx_metadata_end_of_burst.argtypes = [ptr, C.POINTER(C.c_bool)]

        self.dll.uhd_rx_metadata_more_fragments.restype = C.c_int
        self.dll.uhd_rx_metadata_more_fragments.argtypes = [ptr, C.POINTER(C.c_bool)]

        # Metadata: TX
        self.dll.uhd_tx_metadata_make.restype = C.c_int
        self.dll.uhd_tx_metadata_make.argtypes = [C.POINTER(ptr), C.c_bool, C.c_int64, C.c_double, C.c_bool, C.c_bool]

        self.dll.uhd_tx_metadata_free.restype = C.c_int
        self.dll.uhd_tx_metadata_free.argtypes = [C.POINTER(ptr)]
