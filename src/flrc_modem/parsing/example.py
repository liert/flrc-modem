"""插件示例：只追加载荷摘要，不解释任何设备命令。"""
from ..models import DecodedFrame


def describe_payload(frame: DecodedFrame):
    return {"byte_count": len(frame.packet.payload), "first_four_bytes": frame.packet.payload[:4].hex()}
