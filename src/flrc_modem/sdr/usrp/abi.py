"""UHD C API 的 ctypes 结构体、枚举与句柄类型定义。"""
import ctypes as C
from enum import IntEnum


# ---------------------------------------------------------------------------
# 错误码与枚举
# ---------------------------------------------------------------------------
class UHDErrorCode(IntEnum):
    NONE = 0
    INVALID_DEVICE = 1
    INDEX = 10
    KEY = 11
    NOT_IMPLEMENTED = 20
    USB = 21
    IO = 30
    OS = 31
    ASSERTION = 40
    LOOKUP = 41
    TYPE = 42
    VALUE = 43
    RUNTIME = 44
    ENVIRONMENT = 45
    SYSTEM = 46
    EXCEPT = 47
    BOOSTEXCEPT = 60
    STDEXCEPT = 70
    UNKNOWN = 100


class UHDStreamMode(IntEnum):
    START_CONTINUOUS = 97       # 'a'
    STOP_CONTINUOUS = 111       # 'o'
    NUM_SAMPS_AND_DONE = 100    # 'd'
    NUM_SAMPS_AND_MORE = 109    # 'm'


class UHDTuneRequestPolicy(IntEnum):
    NONE = 78    # 'N'
    AUTO = 65    # 'A'
    MANUAL = 77  # 'M'


class UHDRxMetadataErrorCode(IntEnum):
    NONE = 0x0
    TIMEOUT = 0x1
    LATE_COMMAND = 0x2
    BROKEN_CHAIN = 0x4
    OVERFLOW = 0x8
    ALIGNMENT = 0xC
    BAD_PACKET = 0xF


class UHDAsyncMetadataEventCode(IntEnum):
    BURST_ACK = 0x1
    UNDERFLOW = 0x2
    SEQ_ERROR = 0x4
    TIME_ERROR = 0x8
    UNDERFLOW_IN_PACKET = 0x10
    SEQ_ERROR_IN_BURST = 0x20
    USER_PAYLOAD = 0x40


# ---------------------------------------------------------------------------
# C 结构体定义
# ---------------------------------------------------------------------------
class StreamArgs(C.Structure):
    _fields_ = [
        ("cpu_format", C.c_char_p),
        ("otw_format", C.c_char_p),
        ("args", C.c_char_p),
        ("channel_list", C.POINTER(C.c_size_t)),
        ("n_channels", C.c_int),
    ]


class StreamCmd(C.Structure):
    _fields_ = [
        ("stream_mode", C.c_int),
        ("num_samps", C.c_size_t),
        ("stream_now", C.c_bool),
        ("time_spec_full_secs", C.c_int64),
        ("time_spec_frac_secs", C.c_double),
    ]


class TuneRequest(C.Structure):
    _fields_ = [
        ("target_freq", C.c_double),
        ("rf_freq_policy", C.c_int),
        ("rf_freq", C.c_double),
        ("dsp_freq_policy", C.c_int),
        ("dsp_freq", C.c_double),
        ("args", C.c_char_p),
    ]


class TuneResult(C.Structure):
    _fields_ = [
        ("clipped_rf_freq", C.c_double),
        ("target_rf_freq", C.c_double),
        ("actual_rf_freq", C.c_double),
        ("target_dsp_freq", C.c_double),
        ("actual_dsp_freq", C.c_double),
    ]
