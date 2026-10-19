"""逐帧写 JSONL；即使业务插件失败也保留已通过 CRC 的原始载荷。"""
import json
from collections.abc import Mapping

from ..models import DecodedFrame
from ..parsing.api import FrameParser


def build_record(frame: DecodedFrame, parser: FrameParser | None = None) -> dict:
    record = frame.to_record()
    if parser is not None:
        try:
            application = parser(frame)
            if application is not None:
                if not isinstance(application, Mapping):
                    raise TypeError("插件应返回 Mapping 或 None")
                # 在写出前检查插件输出，避免不可序列化字段中断原始数据输出。
                json.dumps(application, allow_nan=False)
                record["application"] = dict(application)
        except Exception as error:
            record["application_error"] = f"{type(error).__name__}: {error}"
    return record


def write_jsonl(stream, record: dict):
    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()


def format_frame(frame: DecodedFrame) -> str:
    return (f"[{frame.time_s * 1000:10.3f} ms] {frame.frequency_hz / 1e6:.6f} MHz "
            f"CFO={frame.cfo_hz / 1000:+.2f} kHz Sync={frame.packet.sync_word.hex()} "
            f"LEN={len(frame.packet.payload)} CRC24=OK\n"
            f"  {frame.packet.payload.hex(' ')}")
