"""BLE identification is grounded in recorded IQ and never creates FLRC frames."""
import base64
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import resample_poly

from flrc_modem.io.iq import decode_samples
from webui.backend.app.spectrum.signal_diagnostics import _advertisement, detect_ble_advertisement


@pytest.fixture
def recording():
    fixture = json.loads((Path(__file__).parent / 'fixtures/ble_advertisement.json').read_text())
    raw = base64.b64decode(fixture['iq_base64'])
    assert hashlib.sha256(raw).hexdigest() == fixture['iq_sha256']
    return fixture, decode_samples(raw, fixture['iq_format'])


@pytest.mark.parametrize('up,down', [(1, 1), (1, 2), (2, 3)])
@pytest.mark.parametrize('inverted', [False, True])
def test_recorded_ble_passes_crc_after_resampling_and_frequency_translation(recording, up, down, inverted):
    fixture, iq = recording
    sample_rate = fixture['sample_rate'] * up / down
    iq = resample_poly(iq, up, down) if up != down else iq
    if inverted:
        iq = iq.conj()
    offset = 60_000
    iq = iq * np.exp(2j * np.pi * offset * np.arange(len(iq)) / sample_rate)
    advertisement = detect_ble_advertisement(iq, sample_rate, offset)
    assert advertisement is not None
    assert advertisement.channel == fixture['channel']
    assert advertisement.pdu.hex() == fixture['pdu_hex']
    assert advertisement.crc.hex() == fixture['crc_hex']
    assert 'BLE 1M' in advertisement.hint and '2480MHz' in advertisement.hint
    # This device puts ASCII in AD type 0x01, not the Local Name AD type.
    assert advertisement.local_name is None


def test_access_address_without_correct_pdu_crc_is_not_a_signal_hint(recording):
    fixture, _ = recording
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(fixture['air_pdu_crc_hex']), np.uint8), bitorder='little')
    assert _advertisement(bits).crc.hex() == fixture['crc_hex']
    for index in (0, 20, len(bits) - 1):
        damaged = bits.copy()
        damaged[index] ^= 1
        assert _advertisement(damaged) is None
    assert _advertisement(bits[:32]) is None


def test_noise_and_too_short_windows_do_not_claim_ble():
    rng = np.random.default_rng(83)
    iq = (rng.normal(size=40_000) + 1j * rng.normal(size=40_000)).astype(np.complex64)
    assert detect_ble_advertisement(iq, 8_000_000) is None
    assert detect_ble_advertisement(iq[:100], 8_000_000) is None
    assert detect_ble_advertisement(iq, 1_000_000) is None
