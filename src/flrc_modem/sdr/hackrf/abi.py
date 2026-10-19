"""与 libhackrf C ABI 对齐的结构及 cdecl 回调类型。"""
import ctypes as C


class Transfer(C.Structure):
    _fields_ = [("device", C.c_void_p), ("buffer", C.POINTER(C.c_uint8)),
                ("buffer_length", C.c_int), ("valid_length", C.c_int),
                ("rx_ctx", C.c_void_p), ("tx_ctx", C.c_void_p)]


class DeviceList(C.Structure):
    _fields_ = [("serial_numbers", C.POINTER(C.c_char_p)),
                ("usb_board_ids", C.POINTER(C.c_int)),
                ("usb_device_index", C.POINTER(C.c_int)), ("devicecount", C.c_int),
                ("usb_devices", C.POINTER(C.c_void_p)), ("usb_devicecount", C.c_int)]


SampleCallback = C.CFUNCTYPE(C.c_int, C.POINTER(Transfer))
FlushCallback = C.CFUNCTYPE(None, C.c_void_p, C.c_int)
CompleteCallback = C.CFUNCTYPE(None, C.POINTER(Transfer), C.c_int)


class M0State(C.Structure):
    _fields_ = [("requested_mode", C.c_uint16), ("request_flag", C.c_uint16)] + [
        (name, C.c_uint32) for name in ("active_mode", "m0_count", "m4_count", "num_shortfalls",
                                       "longest_shortfall", "shortfall_limit", "threshold", "next_mode", "error")]
