"""DK32S 天空端 v0.1.5 的无线协议，分离外层整数时间戳与业务字段。"""
from ...models import DecodedFrame
from .common import (BAUD_RATES, base_result, pairing_fields, require_size,
                     unpack_bits_le, unpack_radio)

COMMAND_NAMES = {
    0x11: "遥控通道", 0x12: "失控保护", 0x13: "配对参数", 0x14: "配对阶段处理",
    0x15: "配对确认处理", 0x16: "遥控/数据回传", 0x17: "时间修正", 0x18: "模式同步",
    0x19: "电源电压校准", 0x20: "PWM映射设置", 0x21: "PWM映射查询", 0x22: "固件版本",
    0x23: "频道表查询", 0x24: "接收机电压校准", 0x26: "自动配对查询", 0x28: "自动配对设置",
    0x30: "端口功能", 0x32: "输出模式查询", 0x34: "输出模式设置", 0x36: "输出模式通知",
    0x38: "飞控类型设置", 0x40: "波特率设置", 0x42: "空回调", 0x44: "电压回传查询",
    0x45: "电压回传设置", 0x46: "数据及电压回传", 0x50: "电压告警通知",
    0x52: "副配对查询", 0x53: "副配对状态通知", 0x54: "副配对设置", 0x55: "释放遥控中继",
    0x56: "功率档位设置", 0x58: "天线设置", 0x59: "天线查询", 0x5D: "链路活动刷新",
    0x5E: "扩展遥控通道", 0x5F: "无线教练", 0x60: "一控多机配对", 0x62: "天空端视频设置",
    0x64: "探照灯查询", 0x66: "探照灯设置", 0xF0: "扫描与版本", 0xF1: "配对忙状态",
}
SETTINGS = {
    0x28: "auto_pairing", 0x34: "signal_mode", 0x38: "fcu_type", 0x40: "baud_index",
    0x45: "voltage_reporting", 0x54: "vice_pairing", 0x56: "tx_power_level", 0x58: "antenna_mode",
}
QUERIES = {0x26: "auto_pairing", 0x32: "signal_mode", 0x44: "voltage_reporting",
           0x52: "vice_pairing", 0x59: "antenna_mode"}


def _channels(data: bytes) -> dict:
    require_size(data, 22, "16路通道位流")
    raw = unpack_bits_le(data, 16, 11)
    # +500 是固件输出层的数值转换；此层不臆测成 SBUS 原始值或固定物理单位。
    return {"channels_raw": raw, "channels_output": [value + 500 for value in raw]}


def _rc_data(data: bytes, multi: bool) -> dict:
    prefix = 24 if multi else 22
    if len(data) < prefix:
        raise ValueError(f"遥控载荷不足 {prefix}B")
    result = {"kind": "rc_channels", "rc_mode": "multi" if multi else "normal"}
    if multi:
        result.update(target_device_mask=data[0], device_number=data[1])
    result.update(_channels(data[2:24] if multi else data[:22]))
    if len(data) > prefix:
        result.update(data_batch_id=data[prefix], stream_data_hex=data[prefix + 1:].hex())
    return result


def _multi_pairing(data: bytes, downlink: bool) -> dict:
    require_size(data, 34 if downlink else 10, "一控多机配对")
    result = {"kind": "multi_air_pairing_offer" if downlink else "multi_air_pairing_ack",
              "stage": data[0], "device_number": data[1],
              "rc_address": f"0x{int.from_bytes(data[2:6], 'little'):08X}",
              "receiver_address": f"0x{int.from_bytes(data[6:10], 'little'):08X}"}
    if downlink:
        result.update(filter_address=f"0x{int.from_bytes(data[10:14], 'little'):08X}",
                      channel_indices=list(data[14:]))
    return result


def _pwm_mapping(data: bytes) -> dict:
    require_size(data, 12, "PWM映射块")
    return {"kind": "pwm_mapping", "channel_mapping": unpack_bits_le(data, 9, 5),
            "unused_tail_hex": data[6:].hex()}


def _searchlight(data: bytes) -> dict:
    require_size(data, 3, "探照灯配置")
    return {"kind": "searchlight_config", "enable": data[0],
            "control_channel": data[1], "brightness_level": data[2]}


def _fields(compact, downlink: bool, multi: bool) -> dict:
    cmd, data = compact.command, compact.data
    if cmd == 0x13:
        return pairing_fields(data, offer=downlink)
    if downlink and cmd in (0x11, 0x16) and not compact.is_ack:
        return _rc_data(data, multi)
    if not downlink and cmd in (0x16, 0x46):
        minimum = 3 if cmd == 0x46 else 1
        if len(data) < minimum:
            raise ValueError("数据回传缺少批次号或电压字段")
        result = {"kind": "telemetry", "data_batch_id": data[0],
                  "stream_data_hex": data[1:-2].hex() if cmd == 0x46 else data[1:].hex()}
        if cmd == 0x46:
            result["voltage_raw"] = int.from_bytes(data[-2:], "little")
        return result
    if cmd == 0x17:
        require_size(data, 4, "时间修正")
        ticks = int.from_bytes(data, "little", signed=True)
        return {"kind": "time_correction", "correction_ticks": ticks, "correction_sec": ticks / 10000}
    if cmd == 0x18:
        require_size(data, 2, "模式同步")
        return {"kind": "mode_sync", "mode": data[0], "sync_count": data[1]}
    if cmd == 0x12:
        if not downlink:
            require_size(data, 1, "失控保护确认")
            return {"kind": "ack", "status": data[0]}
        if len(data) < 3:
            raise ValueError("失控保护缺少模式或通道掩码")
        count = (len(data) - 3) * 8 // 11
        if count > 16:
            raise ValueError("失控保护通道数超过16")
        raw = unpack_bits_le(data[3:], count, 11)
        mask = int.from_bytes(data[1:3], "little")
        return {"kind": "failsafe", "mode": data[0], "channel_mask": mask,
                "channel_enabled": [bool(mask & (1 << i)) for i in range(16)],
                "channels_raw": raw, "channels_output": [value + 500 for value in raw]}
    if cmd in (0x19, 0x24):
        require_size(data, 4 if downlink else 1, "电压校准")
        return ({"kind": "voltage_calibration", "input_voltage_mv": int.from_bytes(data, "little")}
                if downlink else {"kind": "ack", "status": data[0]})
    if cmd == 0x20:
        if downlink:
            return _pwm_mapping(data)
        require_size(data, 1, "PWM设置确认")
        return {"kind": "ack", "status": data[0]}
    if cmd == 0x21 and not downlink:
        return _pwm_mapping(data)
    if cmd == 0x22 or (cmd == 0xF0 and not downlink):
        require_size(data, 4, "固件版本")
        return {"kind": "firmware_version", "version_hex": f"0x{int.from_bytes(data, 'little'):08X}"}
    if cmd == 0x23 and not downlink:
        require_size(data, 20, "频道表")
        return {"kind": "channel_table", "channel_indices": list(data)}
    if (cmd in SETTINGS and downlink) or (cmd in QUERIES and not downlink):
        require_size(data, 1, "配置值")
        key = SETTINGS[cmd] if downlink else QUERIES[cmd]
        result = {"kind": "config_set" if downlink else "config_value", key: data[0]}
        if key == "baud_index":
            result["baud_rate"] = BAUD_RATES[data[0]] if data[0] < len(BAUD_RATES) else None
        return result
    if cmd in SETTINGS and not downlink:
        require_size(data, 0, "配置确认")
        return {"kind": "ack"}
    if cmd == 0x30:
        require_size(data, 3, "端口功能")
        return {"kind": "port_function", "operation": data[0], "data1_function": data[1], "data2_function": data[2]}
    if cmd in (0x36, 0x50, 0x53) and not downlink:
        require_size(data, 1, "状态通知")
        return {"kind": "notification", "value": data[0]}
    if cmd == 0x5E and downlink:
        require_size(data, 26, "扩展遥控通道")
        return {"kind": "extended_rc_channels", "prefix_hex": data[:4].hex(), **_channels(data[4:])}
    if cmd == 0x5F:
        require_size(data, 23 if downlink else 1, "无线教练模式")
        return {"kind": "wireless_trainer", "enable": data[0], **(_channels(data[1:]) if downlink else {})}
    if cmd == 0x60:
        return _multi_pairing(data, downlink)
    if cmd == 0x62:
        require_size(data, 12 if downlink else 0, "天空端视频设置")
        return ({"kind": "video_config", "parameters_u32": [int.from_bytes(data[i:i + 4], "little") for i in range(0, 12, 4)]}
                if downlink else {"kind": "ack"})
    if (cmd == 0x64 and not downlink) or (cmd == 0x66 and downlink):
        return _searchlight(data)
    if cmd == 0x66 and not downlink:
        require_size(data, 0, "空确认")
        return {"kind": "ack"}
    if cmd == 0x55:
        if not downlink:
            require_size(data, 0, "空确认")
            return {"kind": "ack"}
        if len(data) == 1:
            return {"kind": "release_rc_relay", "vice_pairing": data[0]}
        require_size(data, 0, "释放遥控中继")
        return {"kind": "release_rc_relay"}
    # 只保留已识别但无业务字段的命令名称，不为未知内容编造结构。
    return {"kind": "unknown", "raw_hex": data.hex()}


def _parse(frame: DecodedFrame, multi: bool) -> dict:
    compact = unpack_radio(frame.packet.payload, (1, 5))
    downlink = compact.timestamp_ticks is not None
    result = base_result("dk32s", compact, "ground_to_air" if downlink else "air_to_ground",
                         COMMAND_NAMES.get(compact.command, "未知命令"))
    result.update(wire_layout="timestamp_prefix" if downlink else "compact_only",
                  rc_mode="multi" if multi else "normal", fields=_fields(compact, downlink, multi))
    if downlink:
        result.update(timestamp_ticks=compact.timestamp_ticks, timestamp_sec=compact.timestamp_ticks / 10000)
    return result


def parse(frame: DecodedFrame) -> dict:
    """普通遥控布局；不按载荷长度猜测一控多机前缀。"""
    return _parse(frame, multi=False)


def parse_multi(frame: DecodedFrame) -> dict:
    """一控多机遥控布局：明确增加目标位图和设备编号两个字节。"""
    return _parse(frame, multi=True)
