"""USRP 设备探测与属性解析工具。"""
import ctypes as C

from .library import UHDLibrary


def parse_device_record(raw_record: str) -> dict:
    """将 UHD 设备描述字符串 (key=val,key=val) 解析为字典结构。"""
    device = {}
    for part in raw_record.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            device[k.strip()] = v.strip()
        else:
            device[part] = True
    return device


def discover_usrp_devices(library: UHDLibrary | None = None, args: str = "") -> list[dict]:
    """使用 uhd_usrp_find 探测所有连接的 USRP 设备。"""
    lib = library or UHDLibrary()
    vec = C.c_void_p()
    lib.dll.uhd_string_vector_make(C.byref(vec))
    try:
        args_bytes = args.encode("utf-8") if isinstance(args, str) else b""
        code = lib.dll.uhd_usrp_find(args_bytes, C.byref(vec))
        lib.check(code, "uhd_usrp_find")

        size = C.c_size_t(0)
        lib.dll.uhd_string_vector_size(vec, C.byref(size))

        results = []
        buf = C.create_string_buffer(1024)
        for i in range(size.value):
            lib.dll.uhd_string_vector_at(vec, i, buf, 1024)
            record_str = buf.value.decode("utf-8", errors="replace")
            parsed = parse_device_record(record_str)
            parsed["device_index"] = i
            parsed["raw"] = record_str
            results.append(parsed)
        return results
    finally:
        lib.dll.uhd_string_vector_free(C.byref(vec))
