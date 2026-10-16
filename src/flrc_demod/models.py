"""层间数据结构：CRC 通过的物理帧不需要知道命令号和应用长度。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PhysicalPacket:
    sync_word: bytes
    header: bytes
    payload: bytes
    crc: bytes
    fec_path_metric: float


@dataclass(frozen=True)
class DecodedFrame:
    time_s: float
    frequency_hz: float
    cfo_hz: float
    sample_power_db: float
    correlation_score: float
    preamble_errors: int
    packet: PhysicalPacket

    def to_record(self) -> dict:
        """稳定的 JSONL 接口；业务插件只能增加独立的 application 字段。"""
        return {
            "schema_version": 1,
            "time_s": self.time_s,
            "frequency_hz": self.frequency_hz,
            "cfo_hz": self.cfo_hz,
            "sample_power_db": self.sample_power_db,
            "correlation_score": self.correlation_score,
            "preamble_errors": self.preamble_errors,
            "sync_word_hex": self.packet.sync_word.hex(),
            "hardware_header_hex": self.packet.header.hex(),
            "payload_length": len(self.packet.payload),
            "payload_hex": self.packet.payload.hex(),
            "crc_hex": self.packet.crc.hex(),
            "crc_ok": True,
            "fec_path_metric": self.packet.fec_path_metric,
        }
