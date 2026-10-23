"""SIYI 2.4G DataLink 无线小协议；F2 首部和 DK32S 不同。"""
from ...models import DecodedFrame
from .common import (BAUD_RATES, base_result, float32_seconds, pairing_fields,
                     require_size, unpack_radio)

COMMAND_NAMES = {
    0x13: "配对参数", 0x14: "配对阶段确认", 0x15: "配对确认",
    0x16: "链路保活", 0x17: "传输延时", 0x18: "工作模式同步",
    0xF0: "扫描与版本", 0xF2: "数传透传", 0xF3: "飞控类型",
    0xF5: "自动连接配置", 0xF7: "串口波特率",
}


def parse(frame: DecodedFrame) -> dict:
    return parse_payload(frame.packet.payload)


def parse_payload(payload: bytes) -> dict:
    """Parse the same wire layout used by the CLI, independently of RF metadata."""
    compact = unpack_radio(payload, (1,))
    cmd, data = compact.command, compact.data
    request = compact.need_ack and not compact.is_ack
    reply = not compact.need_ack and compact.is_ack
    direction = "unknown"
    fields = {"kind": "unknown", "raw_hex": data.hex()}
    if cmd == 0x13 and request:
        fields = pairing_fields(data, offer=True)
        direction = "air_to_ground"
    elif cmd == 0x13 and reply:
        fields = pairing_fields(data, offer=False)
        direction = "ground_to_air"
    elif cmd == 0xF2 and request:
        if len(data) < 5:
            raise ValueError("DataLink 上行 F2 至少包含 4B 浮点时间和 1B 批次号")
        fields = {"kind": "telemetry", "sync_time_sec": float32_seconds(data[:4]),
                  "data_batch_id": data[4], "stream_data_hex": data[5:].hex()}
        direction = "air_to_ground"
    elif cmd == 0xF2 and reply:
        if not data:
            raise ValueError("DataLink 下行 F2 缺少批次号")
        fields = {"kind": "telemetry", "data_batch_id": data[0], "stream_data_hex": data[1:].hex()}
        direction = "ground_to_air"
    elif cmd in (0xF3, 0xF5, 0xF7) and reply:
        require_size(data, 1, "DataLink 配置值")
        fields = {"kind": "config_set", "config_value": data[0]}
        if cmd == 0xF3:
            fields['fcu_type'] = data[0]
        if cmd == 0xF7:
            fields['baud_index'] = data[0]
            fields["baud_rate"] = BAUD_RATES[data[0]] if data[0] < len(BAUD_RATES) else None
        # 此处 B=1 也可能是配置下发，不能把 is_ack 直接当成天空端方向。
        direction = "ground_to_air"
    elif cmd == 0x17 and (request or reply):
        fields = {"kind": "transfer_delay", "transfer_delay_sec": float32_seconds(data)}
        direction = "air_to_ground" if request else "ground_to_air"
    elif cmd == 0x18 and len(data) == 2:
        fields = {"kind": "mode_sync", "mode": data[0], "sync_count": data[1]}
        if reply:
            direction = 'ground_to_air'
    elif cmd == 0xF0 and len(data) == 4:
        fields = {"kind": "firmware_version", "version_hex": f"0x{int.from_bytes(data, 'little'):08X}"}
    # 尚未确认的配置确认载荷、14/15/16 等保留原始字节，不套用 DK32S 含义。
    result = base_result("datalink", compact, direction, COMMAND_NAMES.get(cmd, "未知命令"))
    result.update(wire_layout="compact_only", fields=fields)
    return result
