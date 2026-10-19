"""枚举与选择设备；成功打开才视为可用，不靠枚举结果猜测占用状态。"""
import ctypes as C
from contextlib import contextmanager

from .library import HackRFError


@contextmanager
def device_listing(api):
    """调用者持有 libhackrf 初始化引用；列表在任何退出路径都释放。"""
    listing = api.hackrf_device_list()
    if not listing:
        raise HackRFError("hackrf_device_list 返回空指针")
    try:
        yield listing
    finally:
        api.hackrf_device_list_free(listing)


def device_records(listing):
    result = []
    for index in range(listing.contents.devicecount):
        raw = listing.contents.serial_numbers[index] if listing.contents.serial_numbers else None
        result.append({"index": index, "serial": raw.decode(errors="replace") if raw else None})
    return result


def describe_device(record):
    return f"index={record['index']}，serial={record['serial'] or '无法读取'}"


def open_selected_device(api, output, *, serial=None, device_index=None):
    with device_listing(api) as listing:
        records = device_records(listing)
        if not records:
            raise HackRFError("未发现 HackRF 设备；请检查连接、驱动及当前 DLL 的设备枚举结果")
        if serial is not None:
            matches = [record for record in records if record["serial"] is not None
                       and record["serial"].lower().endswith(serial.lower())]
            if not matches:
                raise HackRFError(f"找不到序列号或后缀 {serial}；已枚举："
                                  + "；".join(describe_device(record) for record in records))
            if len(matches) != 1:
                raise HackRFError(f"序列号后缀 {serial} 匹配多台设备，请使用完整序列号或 --device-index："
                                  + "；".join(describe_device(record) for record in matches))
        elif device_index is not None:
            if device_index >= len(records):
                raise HackRFError(f"设备索引 {device_index} 超出范围，当前索引为 0..{len(records) - 1}")
            matches = [records[device_index]]
        else:
            matches = records
        errors = []
        for record in matches:
            candidate = C.c_void_p()
            code = api.hackrf_device_list_open(listing, record["index"], C.byref(candidate))
            try:
                api.check(code, f"hackrf_device_list_open({describe_device(record)})")
                if not candidate.value:
                    raise HackRFError("库报告打开成功但返回空设备句柄")
            except HackRFError as error:
                errors.append(str(error))
                continue
            C.cast(output, C.POINTER(C.c_void_p))[0] = candidate
            return record
        mode = "指定设备无法打开（不切换到其他设备）" if serial is not None or device_index is not None else "没有可打开的 HackRF 设备"
        raise HackRFError(mode + "；设备可能被占用，或存在驱动/USB 访问问题：\n" + "\n".join(errors))
