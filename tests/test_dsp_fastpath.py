"""实时解调快路径：JIT Viterbi 与参考实现一致、同步字前置门限、候选去重/限额与噪声门限。"""
import numpy as np
import pytest

from flrc_modem.config import DemodConfig
from flrc_modem.dsp import detector as detector_module
from flrc_modem.dsp.detector import BurstDemodulator
from flrc_modem.dsp.frontend import Frontend
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.phy import fec
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.phy.sync import PREFIX_BITS, expected_sync_raw_bits, sync_raw_errors

SAMPLE_RATE = 2_500_000.0
FREQUENCY = 2_471_660_000
SYNC = bytes.fromhex("ecee6698")
OTHER = bytes.fromhex("54455354")
PAYLOAD = bytes(range(40))


def _iq(sync=SYNC, payload=PAYLOAD, noise=0.02, seed=0):
    packet = encode_packet(payload, sync, coding_rate="3/4")
    config = ModulationConfig(sample_rate=SAMPLE_RATE, bit_rate=325_000, coding_rate="3/4",
                              bt=1.0, offset_hz=0, guard_seconds=0.004)
    signal = np.concatenate([np.asarray(chunk, dtype=np.complex64) for chunk in iter_signal(packet.bits, config)])
    rng = np.random.default_rng(seed)
    return signal + (noise * (rng.normal(size=len(signal)) + 1j * rng.normal(size=len(signal)))).astype(np.complex64)


def _config(**options):
    return DemodConfig(sample_rate=SAMPLE_RATE, center_frequency=FREQUENCY, frequencies=(FREQUENCY,),
                       bit_rate=325_000, chunk_seconds=0.010, **options)


@pytest.mark.parametrize("rate", ["1/2", "3/4", "1"])
@pytest.mark.parametrize("terminated", [True, False])
def test_jit_viterbi_matches_reference_on_noisy_codewords(rate, terminated):
    rng = np.random.default_rng(7)
    for steps in (1, 2, 3, 7, 64, 175, 1062):
        data = rng.integers(0, 2, steps).astype(np.uint8)
        if terminated and rate != "1":
            data[-6:] = 0
        coded = fec.convolutional_encode(data, rate)
        noisy = coded ^ (rng.random(len(coded)) < 0.04).astype(np.uint8)
        for bits, count in ((coded, steps), (noisy, steps), (noisy, None)):
            expected = fec._convolutional_decode_reference(bits, count, terminated, rate)
            actual = fec.convolutional_decode(bits, count, terminated, rate)
            np.testing.assert_array_equal(actual[0], expected[0])
            assert actual[1] == expected[1]


def test_jit_viterbi_keeps_input_validation():
    with pytest.raises(ValueError):
        fec.convolutional_decode([0, 1], steps=100)
    fec.warm_up()
    fec.warm_up()  # 幂等


def test_expected_sync_raw_bits_match_air_bits_for_both_polarities():
    inverted = bytes(byte ^ 0xFF for byte in SYNC)
    expected = expected_sync_raw_bits((SYNC,))
    for word in (SYNC, inverted):
        raw = encode_packet(PAYLOAD, word).bits[PREFIX_BITS - 32:PREFIX_BITS]
        assert sync_raw_errors(raw[None, :], expected)[0] == 0
    raw = encode_packet(PAYLOAD, OTHER).bits[PREFIX_BITS - 32:PREFIX_BITS]
    assert sync_raw_errors(raw[None, :], expected)[0] > 4
    damaged = encode_packet(PAYLOAD, SYNC).bits[PREFIX_BITS - 32:PREFIX_BITS].copy()
    damaged[[3, 9, 17, 30]] ^= 1
    assert sync_raw_errors(damaged[None, :], expected)[0] == 4


def _count_decodes(monkeypatch):
    calls = []
    original = detector_module.decode_packet

    def counting(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(detector_module, "decode_packet", counting)
    return calls


def test_sync_gate_skips_fec_for_foreign_sync_words(monkeypatch):
    calls = _count_decodes(monkeypatch)
    iq = _iq(sync=OTHER)
    frames, groups = BurstDemodulator(_config(sync_word=SYNC)).demodulate(iq, FREQUENCY)
    assert frames == [] and groups == 0 and calls == []
    frames, _ = BurstDemodulator(_config(sync_word=SYNC, max_sync_errors=32)).demodulate(iq, FREQUENCY)
    assert frames == [] and calls  # 关闭门限时回到原先由 CRC 拒绝的路径


def test_sync_gate_keeps_matching_and_unconfigured_decodes():
    iq = _iq()
    for options in ({"sync_word": SYNC}, {}, {"sync_word": OTHER, "sync_words": (SYNC,)}):
        frames, _ = BurstDemodulator(_config(**options)).demodulate(iq, FREQUENCY)
        assert [(frame.packet.sync_word, frame.packet.payload) for frame in frames] == [(SYNC, PAYLOAD)], options


def test_burst_attempts_are_deduplicated_and_capped(monkeypatch):
    iq = _iq()
    start = int(0.004 * SAMPLE_RATE) + 2000
    iq[start:start + 600] = 0  # 包中部衰落：所有候选都无法通过 CRC
    calls = _count_decodes(monkeypatch)
    frames, groups = BurstDemodulator(_config(sync_word=SYNC, max_burst_attempts=2)).demodulate(iq, FREQUENCY)
    assert frames == [] and groups == 1
    assert 1 <= len(calls) <= 2
    assert len({bits.tobytes() for bits in calls}) == len(calls)


def test_noise_gate_rejects_pure_noise_but_not_bursts():
    rng = np.random.default_rng(3)
    count = int(0.016 * SAMPLE_RATE)
    frontend = Frontend(_config())
    for _ in range(20):
        noise = (rng.normal(size=count) + 1j * rng.normal(size=count)).astype(np.complex64)
        assert frontend.process(noise, 0.0) is None
    assert frontend.process(_iq(), 0.0) is not None


def test_config_validates_fast_path_limits():
    for options in ({"max_sync_errors": -1}, {"max_sync_errors": 33}, {"max_burst_attempts": -1},
                    {"max_sync_errors": True}, {"max_burst_attempts": 1.5}):
        with pytest.raises(ValueError):
            _config(**options)


def test_frontend_and_detector_jit_match_numpy_fallback(monkeypatch):
    from flrc_modem.dsp import frontend as frontend_module
    iq = _iq(noise=0.08, seed=11)
    cfg = _config(sync_word=SYNC)
    fe_jit = Frontend(cfg).process(iq, 12345.0)
    dem_jit, groups_jit = BurstDemodulator(cfg).demodulate(iq, FREQUENCY)

    monkeypatch.setattr(frontend_module, "_jit_power_stats", None)
    monkeypatch.setattr(detector_module, "_scan_preambles_jit", None)
    fe_np = Frontend(cfg).process(iq, 12345.0)
    dem_np, groups_np = BurstDemodulator(cfg).demodulate(iq, FREQUENCY)

    assert np.allclose(fe_jit[0], fe_np[0], rtol=1e-12, atol=1e-12)
    assert np.allclose(fe_jit[1], fe_np[1], rtol=1e-12, atol=1e-12)
    assert np.isclose(fe_jit[2], fe_np[2], rtol=1e-12) and np.isclose(fe_jit[3], fe_np[3], rtol=1e-12)
    assert groups_jit == groups_np == 1 and len(dem_jit) == len(dem_np) == 1
    a, b = dem_jit[0], dem_np[0]
    assert a.packet == b.packet and a.preamble_errors == b.preamble_errors
    assert np.isclose(a.time_s, b.time_s, atol=1e-12)
    assert np.isclose(a.cfo_hz, b.cfo_hz, atol=1e-9)
    assert np.isclose(a.correlation_score, b.correlation_score, atol=1e-12)
