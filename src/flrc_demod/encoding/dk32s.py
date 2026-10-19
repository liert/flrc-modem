"""DK32S 业务字段 -> 长度/时间戳/紧凑帧；不承担跳频和应答状态机。"""
from math import floor

from ..parsing.protocols.dk32s import SETTINGS


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
    if command in SETTINGS:
        return _byte(fields, SETTINGS[command]) if downlink else b""
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
