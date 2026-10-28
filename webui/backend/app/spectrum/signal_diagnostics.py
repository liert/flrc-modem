"""Identify CRC-verified BLE advertising without treating it as a FLRC frame."""
from dataclasses import dataclass

import numpy as np
from scipy.signal import firwin, oaconvolve, resample_poly


_ACCESS_BITS = np.unpackbits(np.frombuffer(bytes.fromhex('d6be898e'), np.uint8), bitorder='little')
_ACCESS_SIGNS = 2 * _ACCESS_BITS.astype(np.float32) - 1
_CHANNEL_FREQUENCIES = {37: 2402, 38: 2426, 39: 2480}


@dataclass(frozen=True)
class BLEAdvertisement:
    channel: int
    pdu: bytes
    crc: bytes

    @property
    def local_name(self) -> str | None:
        # Legacy advertising/scan-response PDUs carry AdvA followed by AD structures.
        if self.pdu[0] & 0x0F not in (0, 2, 4, 6):
            return None
        data = self.pdu[8:]
        offset = 0
        while offset < len(data):
            size = data[offset]
            if size < 1 or offset + size >= len(data):
                break
            if data[offset + 1] in (8, 9):
                name = data[offset + 2:offset + 1 + size].decode('utf-8', errors='replace')
                return ''.join(char for char in name if char.isprintable()) or None
            offset += size + 1
        return None

    @property
    def hint(self) -> str:
        name = f'，名称 {self.local_name}' if self.local_name else ''
        return (f'检测到 CRC 正确的 BLE 1M 广播（信道 {self.channel} / '
                f'{_CHANNEL_FREQUENCIES[self.channel]}MHz{name}）。当前使用 FLRC 解调，'
                '这些广播不会产生 FLRC 帧；请核对目标设备与实际发射频点。')


def _advertisement(raw_bits: np.ndarray) -> BLEAdvertisement | None:
    for channel in (37, 38, 39):
        # BLE transmits each octet LSB first. Whitening starts at the PDU header.
        state = channel | 0x40
        bits = raw_bits.copy()
        for i in range(len(bits)):
            if state & 1:
                bits[i] ^= 1
                state ^= 0x88
            state >>= 1
        data = np.packbits(bits[:len(bits) // 8 * 8], bitorder='little').tobytes()
        if len(data) < 5 or data[0] & 0x0F > 7:
            continue
        size = 2 + data[1]
        if len(data) < size + 3:
            continue
        # Bit-reflected form of CRCInit=0x555555 and polynomial 0x00065B.
        crc = 0xAAAAAA
        for byte in data[:size]:
            for _ in range(8):
                feedback = (crc ^ byte) & 1
                crc >>= 1
                byte >>= 1
                if feedback:
                    crc ^= 0xDA6000
        if data[size:size + 3] == crc.to_bytes(3, 'little'):
            return BLEAdvertisement(channel, data[:size], data[size:size + 3])
    return None


def detect_ble_advertisement(iq: np.ndarray, sample_rate: float,
                             offset_hz: float = 0.0) -> BLEAdvertisement | None:
    """Probe one bounded window around the selected demodulation frequency.

    Access-address correlation only selects candidates; a complete, dewhitened
    PDU must pass the advertising CRC24 before any signal hint is shown.
    """
    if sample_rate < 2_000_000 or len(iq) < sample_rate * .00008:
        return None
    # At most 50ms and 2..8 samples/symbol, independent of the FLRC template.
    samples = np.asarray(iq[:round(sample_rate * .05)], dtype=np.complex64)
    samples = samples - samples.mean()
    if offset_hz:
        samples = (samples * np.exp(-2j * np.pi * offset_hz * np.arange(len(samples)) / sample_rate)).astype(np.complex64)
    decimation = max(1, int(sample_rate // 4_000_000))
    if decimation > 1:
        samples = resample_poly(samples, 1, decimation)
    work_rate = sample_rate / decimation
    taps = firwin(41, 750_000, fs=work_rate).astype(np.float32)
    samples = oaconvolve(samples, taps)[:len(samples)]
    sps = work_rate / 1_000_000
    for phase in range(int(np.ceil(sps))):
        positions = phase + np.arange(int((len(samples) - phase - 2) / sps)) * sps
        indices = positions.astype(np.int64)
        fractions = positions - indices
        symbols = samples[indices] * (1 - fractions) + samples[indices + 1] * fractions
        discriminator = np.angle(symbols[1:] * symbols[:-1].conj())
        bits = (discriminator > 0).astype(np.uint8)
        correlation = np.correlate(2 * bits.astype(np.float32) - 1, _ACCESS_SIGNS, mode='valid')
        # Both IQ polarities are allowed, but never relax the CRC.
        for start in np.flatnonzero(np.abs(correlation) == 32)[:32]:
            raw = bits[start + 32:start + 32 + (2 + 255 + 3) * 8].copy()
            if correlation[start] < 0:
                raw ^= 1
            if len(raw) >= 40:
                advertisement = _advertisement(raw)
                if advertisement is not None:
                    return advertisement
    return None
