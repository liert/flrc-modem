"""On-demand protocol inspection; no radio, database, or synthetic PHY validation."""
import re

from .. import __version__
from .api import load_payload_parser


MAX_PAYLOAD_BYTES = 1024


def decode_hex(text: str) -> bytes:
    # Reject punctuation/odd nibbles rather than silently changing pasted content.
    compact = re.sub(r"\s", "", text)
    if not compact or len(compact) % 2 or re.fullmatch(r"[0-9a-fA-F]+", compact) is None:
        raise ValueError("请输入完整物理载荷 Hex：只允许十六进制字符、空格或换行，且每字节必须有两位")
    if len(compact) > MAX_PAYLOAD_BYTES * 2:
        raise ValueError(f"单次解析最多 {MAX_PAYLOAD_BYTES} 字节")
    return bytes.fromhex(compact)


def _layout(payload: bytes, app: dict) -> list[dict]:
    """Offsets are relative to physical payload, never to a guessed full RF packet."""
    offset = 5 if app["wire_layout"] == "timestamp_prefix" else 1
    spans = []

    def add(path, label, start, length, value, **extra):
        if length > 0:
            spans.append({"path": path, "label": label, "offset": start, "length": length,
                          "value": value, "raw_hex": payload[start:start + length].hex(), **extra})

    add("software_length", "软件帧长", 0, 1, payload[0])
    if offset == 5:
        add("timestamp_ticks", "时间戳计数（100 μs/计数）", 1, 4, app["timestamp_ticks"])
    add("payload_length", "业务载荷长度", offset, 1, app["payload_length"], bit_offset=0, bit_length=7)
    add("need_ack", "请求 ACK", offset, 1, app["need_ack"], bit_offset=7, bit_length=1)
    add("sequence", "报文序号（小端位域）", offset + 1, 2, app["sequence"], bit_offset=1, bit_length=15)
    add("is_ack", "ACK 标志", offset + 1, 2, app["is_ack"], bit_offset=0, bit_length=1)
    add("command", "命令字 · " + app["command_name"], offset + 3, 1, app["command_hex"])
    add("payload_hex", "业务载荷", offset + 4, app["payload_length"], app["payload_hex"])
    add("sum8", "业务 sum8 校验", len(payload) - 1, 1, payload[-1])
    return spans


def inspect_payload(payload_hex: str, protocol: str) -> dict:
    parser = load_payload_parser(protocol)
    payload = decode_hex(payload_hex)
    result = {"schema_version": 1, "parser_version": __version__, "protocol": protocol,
              "payload_hex": payload.hex(), "payload_length": len(payload),
              "physical_crc_status": "not_checked", "application": None,
              "ranges": [], "error": None, "status": "error"}
    try:
        app = dict(parser(payload))
    except ValueError as error:
        result["error"] = str(error)
        return result
    result.update(application=app, ranges=_layout(payload, app),
                  status="partial" if app.get("fields", {}).get("kind") == "unknown" else "parsed")
    return result
