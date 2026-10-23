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


def expected_sync_raw_bits(sync_words) -> np.ndarray:
    """已知同步字在判决域（差分前）的 32bit 空口比特，形状 (同步字数, 32)。

    与 recover_sync_candidates 互逆：0x99/0x66 两种候选只差首比特，原字与逐字节取反的
    同步字也只差首比特，因此比较时忽略第 0 位即可同时覆盖两种掩码与两种极性。
    """
    mask = np.unpackbits(np.array([0x99] * 4, dtype=np.uint8))
    rows = []
    for word in sync_words:
        logical = np.unpackbits(np.frombuffer(bytes(word), dtype=np.uint8)) ^ mask
        raw = logical.copy()
        raw[1:] ^= logical[:-1]
        rows.append(raw)
    return np.array(rows, dtype=np.uint8).reshape(len(rows), 32)


def sync_raw_errors(raw_sync, expected) -> np.ndarray:
    """判决域同步区与各已知同步字的最小比特差（不计首比特）。

    判决域误码彼此独立；差分恢复后单个误码会翻转其后全部逻辑比特，所以门限放在差分前。
    """
    raw = np.asarray(raw_sync, dtype=np.uint8)[:, None, 1:32]
    return np.count_nonzero(raw != np.asarray(expected)[None, :, 1:32], axis=2).min(axis=1)


def recover_coded_bits(raw_bits, mask: int):
    # 两个互补相位候选均保留，最终只由硬件头和 CRC 决定是否成立。
    bits = np.bitwise_xor.accumulate(np.asarray(raw_bits, dtype=np.uint8))
    pattern = np.unpackbits(np.array([mask], dtype=np.uint8))
    return bits ^ np.resize(pattern, len(bits))
