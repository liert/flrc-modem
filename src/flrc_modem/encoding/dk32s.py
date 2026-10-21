"""DK32S 业务字段 -> 长度/时间戳/紧凑帧；不承担跳频和应答状态机。"""
from math import floor

from ..parsing.protocols.common import BAUD_RATES
from ..parsing.protocols.dk32s import QUERIES, SETTINGS


def integer(value, low, high, name):
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError:
            raise ValueError(f"{name} 必须为整数或 0x 十六进制字符串") from None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} 必须为 {low}..{high}")
    return value


def _byte(fields, name):
    return bytes([integer(fields.pop(name), 0, 255, name)])


def _hex(value):
    if not isinstance(value, str):
        raise ValueError("十六进制数据必须为字符串")
    return bytes.fromhex(value)


def _channels(fields):
    values = fields.pop("channels_raw")
    if not isinstance(values, list) or len(values) != 16:
        raise ValueError("channels_raw 必须恰好为 16 路原始值，范围 0..2047")
    packed = sum(integer(value, 0, 2047, "channels_raw") << (11 * i) for i, value in enumerate(values))
    return packed.to_bytes(22, "little")


def _business(command, fields, downlink, multi, is_ack):
    # data_hex 是显式原始业务字段入口，仍自动重算外层长度、sum8 和物理 CRC。
    if "data_hex" in fields:
        return _hex(fields.pop("data_hex"))
    if downlink and command in (0x11, 0x16) and not is_ack:
        data = (_byte(fields, "target_device_mask") + _byte(fields, "device_number")) if multi else b""
        data += _channels(fields)
        if "data_batch_id" in fields:
            data += _byte(fields, "data_batch_id") + _hex(fields.pop("stream_data_hex", ""))
        return data
    if not downlink and command in (0x16, 0x46):
        data = _byte(fields, "data_batch_id") + _hex(fields.pop("stream_data_hex", ""))
        if command == 0x46:
            data += integer(fields.pop("voltage_raw"), 0, 65535, "voltage_raw").to_bytes(2, "little")
        return data
    if command == 0x13:
        data = integer(fields.pop("filter_address"), 0, 0xFFFFFFFF, "filter_address").to_bytes(4, "little")
        if downlink:
            channels = fields.pop("channel_indices")
            if not isinstance(channels, list) or len(channels) != 20:
                raise ValueError("channel_indices 必须为 20 个字节")
            data += bytes(integer(value, 0, 255, "channel_indices") for value in channels)
        return data
    if command == 0x17:
        return integer(fields.pop("correction_ticks"), -0x80000000, 0x7FFFFFFF, "correction_ticks").to_bytes(4, "little", signed=True)
    if command == 0x18:
        return _byte(fields, "mode") + _byte(fields, "sync_count")
    if command == 0x54:
        if not downlink:
            return b""
        for key in ("rc_relay", "rc_relay_switch", "vice_match_mode", "vice_pairing"):
            if key in fields:
                return _byte(fields, key)
        return _byte(fields, "vice_pairing")
    if command == 0x55:
        if not downlink:
            return b""
        for key in ("switch_master_slave", "switch_vice", "rc_relay", "vice_pairing", "vice_match_mode", "release_rc_relay"):
            if key in fields:
                return _byte(fields, key)
        return b""
    if command == 0x12:
        if downlink:
            mode = integer(fields.pop("mode", 0), 0, 255, "mode")
            mask = integer(fields.pop("channel_mask", 0xFFFF), 0, 0xFFFF, "channel_mask")
            channels = fields.pop("channels_raw", [1024] * 16)
            if not isinstance(channels, list) or len(channels) != 16:
                raise ValueError("channels_raw 必须恰好为 16 路原始值")
            packed = sum(integer(v, 0, 2047, "channels_raw") << (11 * i) for i, v in enumerate(channels))
            return bytes([mode]) + mask.to_bytes(2, "little") + packed.to_bytes(22, "little")
        return _byte(fields, "status") if "status" in fields else b"\x00"
    if command in (0x19, 0x24):
        if downlink:
            mv = integer(fields.pop("input_voltage_mv", fields.pop("voltage_mv", 0)), 0, 0xFFFFFFFF, "voltage_mv")
            return mv.to_bytes(4, "little")
        return _byte(fields, "status") if "status" in fields else b"\x00"
    if command == 0x20:
        if downlink:
            mapping = fields.pop("channel_mapping", list(range(9)))
            if not isinstance(mapping, list) or len(mapping) != 9:
                raise ValueError("channel_mapping 必须为 9 路通道映射")
            packed = sum(integer(v, 0, 31, "channel_mapping") << (5 * i) for i, v in enumerate(mapping))
            tail = _hex(fields.pop("unused_tail_hex", "00" * 6))
            if len(tail) != 6:
                raise ValueError("unused_tail_hex 必须为 6 个字节十六进制")
            return packed.to_bytes(6, "little") + tail
        return _byte(fields, "status") if "status" in fields else b"\x00"
    if command == 0x30:
        if downlink:
            return _byte(fields, "operation") + _byte(fields, "data1_function") + _byte(fields, "data2_function")
        return b""
    if command in (0x36, 0x50, 0x53):
        if not downlink:
            return _byte(fields, "value") if "value" in fields else _byte(fields, "status")
        return b""
    if command == 0x5C:
        if downlink:
            magic = integer(fields.pop("magic", 0x2BB4B42B), 0, 0xFFFFFFFF, "magic")
            index = integer(fields.pop("index", 1), 0, 255, "index")
            params = fields.pop("params_u32", [0, 0, 0])
            if len(params) != 3:
                raise ValueError("params_u32 必须为 3 个 u32 整数")
            filter_addr = integer(fields.pop("filter_address", 0), 0, 0xFFFFFFFF, "filter_address")
            channels = fields.pop("channel_indices")
            if len(channels) != 20:
                raise ValueError("channel_indices 必须为 20 个跳频索引")
            res = magic.to_bytes(4, "little") + bytes([index])
            for p in params:
                res += integer(p, 0, 0xFFFFFFFF, "param").to_bytes(4, "little")
            res += filter_addr.to_bytes(4, "little")
            res += bytes(integer(c, 0, 255, "channel_indices") for c in channels)
            return res
        return _byte(fields, "status") if "status" in fields else b""
    if command == 0x5E:
        if downlink:
            prefix = _hex(fields.pop("prefix_hex", "00000000"))
            if len(prefix) != 4:
                raise ValueError("prefix_hex 必须为 4 字节十六进制")
            return prefix + _channels(fields)
        return b""
    if command == 0x5F:
        enable = _byte(fields, "enable")
        if downlink:
            channels = fields.pop("channels_raw", [1024] * 16)
            if not isinstance(channels, list) or len(channels) != 16:
                raise ValueError("channels_raw 必须恰好为 16 路原始值")
            packed = sum(integer(v, 0, 2047, "channels_raw") << (11 * i) for i, v in enumerate(channels))
            return enable + packed.to_bytes(22, "little")
        return enable
    if command == 0x60:
        stage = _byte(fields, "stage")
        dev_num = _byte(fields, "device_number")
        rc_addr = integer(fields.pop("rc_address"), 0, 0xFFFFFFFF, "rc_address").to_bytes(4, "little")
        rx_addr = integer(fields.pop("receiver_address"), 0, 0xFFFFFFFF, "receiver_address").to_bytes(4, "little")
        res = stage + dev_num + rc_addr + rx_addr
        if downlink:
            filter_addr = integer(fields.pop("filter_address"), 0, 0xFFFFFFFF, "filter_address").to_bytes(4, "little")
            channels = fields.pop("channel_indices")
            if len(channels) != 20:
                raise ValueError("channel_indices 必须为 20 个字节")
            res += filter_addr + bytes(integer(c, 0, 255, "channel_indices") for c in channels)
        return res
    if command == 0x62:
        if downlink:
            params = fields.pop("parameters_u32", [0, 0, 0])
            if len(params) != 3:
                raise ValueError("parameters_u32 必须为 3 个 u32 整数")
            return b"".join(integer(p, 0, 0xFFFFFFFF, "param").to_bytes(4, "little") for p in params)
        return b""
    if command in (0x64, 0x66):
        if (command == 0x66 and downlink) or (command == 0x64 and not downlink):
            return _byte(fields, "enable") + _byte(fields, "control_channel") + _byte(fields, "brightness_level")
        return b""
    if (command in (0x14, 0x15, 0x21, 0x23, 0x26, 0x32, 0x42, 0x44, 0x52, 0x59, 0x5D, 0xF1) or (command == 0xF0 and downlink)):
        if not downlink and command == 0xF1:
            return bytes([1 if fields.pop("is_busy", False) else 0])
        return b""
    if command in SETTINGS:
        if downlink:
            target_key = SETTINGS[command]
            if target_key not in fields:
                if command == 0x56 and "power_level" in fields:
                    fields[target_key] = fields.pop("power_level")
                elif command == 0x40 and "baud_rate" in fields:
                    b_val = fields.pop("baud_rate")
                    if b_val in BAUD_RATES:
                        fields[target_key] = BAUD_RATES.index(b_val)
            return _byte(fields, target_key)
        return b""
    if command == 0x22 or (command == 0xF0 and not downlink):
        return integer(fields.pop("version_hex"), 0, 0xFFFFFFFF, "version_hex").to_bytes(4, "little")
    raise ValueError(f"命令 0x{command:02X} 暂无字段编码器，请显式提供 fields.data_hex（空业务为 \"\"）")


def encode(message, context):
    """sequence 缺省按全局包号递增，地面发包的时间戳缺省按样本时钟更新。"""
    allowed = {"direction", "command", "sequence", "timestamp_ticks", "need_ack", "is_ack", "rc_mode", "fields"}
    if set(message) - allowed:
        raise ValueError(f"未知 DK32S 字段：{sorted(set(message) - allowed)}")
    try:
        direction = message["direction"]
        if direction not in ("ground_to_air", "air_to_ground"):
            raise ValueError("direction 应为 ground_to_air 或 air_to_ground")
        downlink = direction == "ground_to_air"
        command = integer(message["command"], 0, 255, "command")
        mode = message.get("rc_mode", "normal")
        if mode not in ("normal", "multi"):
            raise ValueError("rc_mode 应为 normal 或 multi")
        flags = [message.get("need_ack", False), message.get("is_ack", False)]
        if any(not isinstance(flag, bool) for flag in flags):
            raise ValueError("need_ack/is_ack 必须为布尔值")
        sequence = integer(message.get("sequence", context.frame_index % 32768), 0, 32767, "sequence")
        fields = message.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError("fields 必须为对象")
        fields = fields.copy()
        data = _business(command, fields, downlink, mode == "multi", flags[1])
        if fields:
            raise ValueError(f"未使用的业务字段：{sorted(fields)}")
        # 固件地面发送紧凑帧上限 110B；天空端上限 114B。
        if len(data) + 5 > (110 if downlink else 114):
            raise ValueError("业务数据超过 DK32S 固件发送长度限制")
        small = bytes([len(data) | (int(flags[0]) << 7)])
        small += ((sequence << 1) | int(flags[1])).to_bytes(2, "little") + bytes([command]) + data
        small += bytes([sum(small) & 255])
        if downlink:
            ticks = message.get("timestamp_ticks", floor(context.start_sample * 10000 / context.sample_rate) % 0x100000000)
            small = integer(ticks, 0, 0xFFFFFFFF, "timestamp_ticks").to_bytes(4, "little") + small
        elif "timestamp_ticks" in message:
            raise ValueError("天空端发包没有外层 timestamp_ticks")
        return bytes([len(small)]) + small
    except KeyError as error:
        raise ValueError(f"缺少 DK32S 字段：{error.args[0]}") from None
