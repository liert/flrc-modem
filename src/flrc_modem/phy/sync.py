"""FLRC 定时前导及差分判决域的恢复，和设备地址语义无关。"""
import numpy as np

TIMING_PREAMBLE = np.unpackbits(np.frombuffer(bytes.fromhex("043ee2"), dtype=np.uint8))[-21:]
PREFIX_BITS = 32 + 21 + 32


def sync_word_for_polarity(sync_word: str | None, inverted: bool = False) -> str | None:
    """Resolve the logical 4B sync word before packet CRC encoding/decoding."""
    if sync_word is None:
        return None
    raw = bytes.fromhex(sync_word)
    if len(raw) != 4:
        raise ValueError('同步字必须为 4B 十六进制')
    return bytes(byte ^ 0xFF for byte in raw).hex() if inverted else raw.hex()


def recover_sync_candidates(raw_sync) -> tuple[bytes, bytes]:
    differential = np.packbits(np.bitwise_xor.accumulate(raw_sync))
    return (differential ^ 0x99).tobytes(), (differential ^ 0x66).tobytes()


def recover_coded_bits(raw_bits, mask: int):
    # 两个互补相位候选均保留，最终只由硬件头和 CRC 决定是否成立。
    bits = np.bitwise_xor.accumulate(np.asarray(raw_bits, dtype=np.uint8))
    pattern = np.unpackbits(np.array([mask], dtype=np.uint8))
    return bits ^ np.resize(pattern, len(bits))
