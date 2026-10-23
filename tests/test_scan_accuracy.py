"""Synthetic RF regressions for continuous coverage, burst accounting and CRC evidence."""
import ctypes as C
import io
import runpy
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from flrc_modem.scan.cancellation import ScanCancelled
from flrc_modem.scan.engine import SpectrumScanner, _merge_adjacent_channels, _plan_hops
from flrc_modem.scan.flrc_verifier import verify_flrc_candidate
from flrc_modem.scan.models import ChannelCandidate, ScanReport
from flrc_modem.scan.psd import compute_psd_waterfall, detect_spectral_peaks, estimate_adaptive_noise_floor

FS = 2_500_000
CENTER = 2_471_660_000


def noise(n, seed=42):
    rng = np.random.default_rng(seed)
    return (.001 * (rng.normal(size=n) + 1j * rng.normal(size=n))).astype(np.complex64)


def detect(iq, n_fft=512):
    f, maximum, average, slices, _ = compute_psd_waterfall(iq, n_fft, FS, CENTER)
    return detect_spectral_peaks(f, maximum, average, estimate_adaptive_noise_floor(average),
                                 samples=iq, time_slices=slices, dc_center_freq=CENTER)


def test_waterfall_compression_keeps_short_bursts_and_tail():
    iq = noise(160037)
    for start, length, offset in ((12345, 128, 350000), (len(iq) - 127, 127, -420000)):
        iq[start:start + length] += np.exp(2j * np.pi * offset * np.arange(length) / FS)
    small = compute_psd_waterfall(iq, 256, FS, CENTER, max_slices=8)
    full = compute_psd_waterfall(iq, 256, FS, CENTER, max_slices=4096)
    np.testing.assert_array_equal(small[1], full[1])
    np.testing.assert_array_equal(small[2], full[2])
    assert len(small[3]) == 8
    for offset in (350000, -420000):
        assert small[1][np.argmin(abs(small[0] - CENTER - offset))] > -16


def test_burst_count_is_not_active_fft_slice_count():
    iq = noise(24000)
    for start in (2000, 11000, 19000):
        iq[start:start + 2000] += np.exp(2j * np.pi * .11 * np.arange(2000))
    candidates = detect(iq)
    assert len(candidates) == 1
    channel = candidates[0]
    assert channel['burst_count'] == 3
    assert channel['active_slices'] > 3
    assert .2 < channel['duty_cycle'] < .4
    assert abs(channel['frequency_hz'] - CENTER - .11 * FS) < FS / 512


def test_adjacent_narrow_carriers_stay_separate():
    iq = noise(32781)
    for offset in (300000, 380000):
        iq += np.exp(2j * np.pi * offset * np.arange(len(iq)) / FS).astype(np.complex64)
    candidates = sorted(detect(iq, 2048), key=lambda candidate: candidate['frequency_hz'])
    assert len(candidates) == 2
    for channel, offset in zip(candidates, (300000, 380000)):
        assert abs(channel['frequency_hz'] - CENTER - offset) < FS / 2048


@pytest.mark.parametrize('length', [12000, 400000])
def test_noise_does_not_become_signals_with_longer_observation(length):
    assert detect(noise(length)) == []


def test_crc_corrects_frequency_and_counts_one_packet():
    iq = runpy.run_path(str(Path(__file__).with_name('test_dsp_fastpath.py')))['_iq']()
    iq *= np.exp(2j * np.pi * 100000 * np.arange(len(iq)) / FS).astype(np.complex64)
    result = verify_flrc_candidate(iq, FS, CENTER, CENTER + 70000, 350000,
                                   candidate_bit_rates=(325000,))
    assert result['is_flrc'] and result['verification_status'] == 'crc'
    assert result['frequency_method'] == 'crc_cfo'
    assert abs(result['frequency_hz'] - CENTER - 100000) < 3000
    report = SpectrumScanner().scan_iq_samples(iq, FS, CENTER, candidate_bit_rates=(325000,))
    assert len(report.channels) == 1
    channel = report.channels[0]
    assert channel.crc_valid_frames == channel.burst_count == 1
    assert channel.active_slices > 1
    assert abs(channel.frequency_hz - CENTER - 100000) < 3000


@pytest.mark.parametrize('rate', [260000, 325000, 650000, 1300000])
def test_supported_bit_rates_keep_one_crc_carrier(rate):
    from flrc_modem.modulation import ModulationConfig, iter_signal
    from flrc_modem.phy.encoder import encode_packet

    fs = 10_000_000
    packet = encode_packet(bytes(range(40)), b'TEST', coding_rate='3/4')
    config = ModulationConfig(sample_rate=fs, bit_rate=rate, coding_rate='3/4',
                              bt=1., offset_hz=0, guard_seconds=.004)
    iq = np.concatenate([np.asarray(chunk, dtype=np.complex64)
                         for chunk in iter_signal(packet.bits, config)])
    iq += 20 * noise(len(iq), seed=0)
    iq *= np.exp(2j * np.pi * 400000 * np.arange(len(iq)) / fs).astype(np.complex64)
    report = SpectrumScanner().scan_iq_samples(iq, fs, CENTER, candidate_bit_rates=(rate,))
    assert len(report.flrc_channels) == 1
    channel = report.flrc_channels[0]
    assert channel.bit_rate == rate and channel.crc_valid_frames == 1
    assert abs(channel.frequency_hz - CENTER - 400000) < 3000


def test_verification_keeps_packet_crossing_chunk_boundary():
    make_iq = runpy.run_path(str(Path(__file__).with_name('test_dsp_fastpath.py')))['_iq']
    packet = make_iq(payload=bytes(range(127)))
    iq = np.concatenate([noise(round(.046 * FS)), packet, noise(round(.06 * FS))])
    result = verify_flrc_candidate(iq, FS, CENTER, CENTER, 350000,
                                   candidate_bit_rates=(325000,))
    assert result['is_flrc']
    assert result['crc_valid_frames'] == result['burst_count'] == 1


def test_verified_channel_excludes_unverified_sync_words(monkeypatch):
    from dataclasses import replace
    from flrc_modem.dsp.detector import BurstDemodulator

    analyze = BurstDemodulator.analyze

    def with_unverified_word(self, *args, **kwargs):
        result = analyze(self, *args, **kwargs)
        return replace(result, sync_words=result.sync_words + (bytes.fromhex('deadbeef'),))

    monkeypatch.setattr(BurstDemodulator, 'analyze', with_unverified_word)
    iq = runpy.run_path(str(Path(__file__).with_name('test_dsp_fastpath.py')))['_iq']()
    result = verify_flrc_candidate(iq, FS, CENTER, CENTER, 350000,
                                   candidate_bit_rates=(325000,))
    assert result['is_flrc']
    assert result['sync_words'] == ['ecee6698']


@pytest.mark.parametrize('options', [{'n_fft': 15}, {'n_fft': 16.0}, {'hop_size': 0}, {'hop_size': 2049}])
def test_fft_options_fail_before_allocating_or_dividing(options):
    with pytest.raises(ValueError):
        compute_psd_waterfall(noise(100), **options)


def test_usrp_initial_discard_preserves_valid_capture_length():
    from flrc_modem.sdr.usrp.config import USRPConfig
    from flrc_modem.sdr.usrp.device import USRPDevice
    from usrp_fake import FakeUHDLibrary

    raw = np.arange(40, dtype=np.int8).tobytes()
    library = FakeUHDLibrary(rx_samples=raw)
    commands = []
    original = library.dll.uhd_rx_streamer_issue_stream_cmd

    def issue(streamer, command):
        commands.append(command._obj.num_samps)
        return original(streamer, command)

    library.dll.uhd_rx_streamer_issue_stream_cmd = issue
    sink = io.BytesIO()
    with USRPDevice(library) as device:
        stats = device.receive(sink, USRPConfig(CENTER, sample_rate=FS, cpu_format='cs8'),
                               sample_count=5, block_samples=4, discard_seconds=3 / FS)
    assert commands[0] == 8
    assert sink.getvalue() == raw[6:16]
    assert stats.sample_count == 5 and stats.byte_count == 10


def test_usrp_cancel_during_settle_does_not_start_stream():
    from flrc_modem.sdr.usrp.config import USRPConfig
    from flrc_modem.sdr.usrp.device import USRPDevice
    from usrp_fake import FakeUHDLibrary

    event = Event()
    library = FakeUHDLibrary()
    library.dll.uhd_rx_streamer_issue_stream_cmd = MagicMock()
    with USRPDevice(library) as device:
        stats = device.receive(io.BytesIO(), USRPConfig(CENTER, sample_rate=FS),
                               sample_count=5, cancel_event=event, settle_seconds=1,
                               rate_callback=lambda rate: event.set())
    assert stats.sample_count == 0
    library.dll.uhd_rx_streamer_issue_stream_cmd.assert_not_called()


def install_device(monkeypatch):
    device = MagicMock()
    device.__enter__.return_value = device
    device.configure_rx.return_value = 10_000_000.
    device.get_master_clock_rate.return_value = 125_000_000.
    captures = []

    def receive(sink, config, sample_count, **kwargs):
        captures.append((config.frequency_hz, kwargs))

        def frequency(handle, channel, output):
            C.cast(output, C.POINTER(C.c_double))[0] = config.frequency_hz
            return 0

        device.api.dll.uhd_usrp_get_rx_freq.side_effect = frequency
        kwargs['rate_callback'](10_000_000.)
        sink.write(np.zeros(4096, dtype=np.complex64).tobytes())
        return SimpleNamespace(overflow_count=0)

    device.receive.side_effect = receive
    monkeypatch.setattr('flrc_modem.sdr.usrp.device.USRPDevice', lambda *args, **kwargs: device)
    monkeypatch.setattr('flrc_modem.sdr.usrp.library.UHDLibrary', MagicMock())
    return device, captures


def install_coarse(scanner):
    scanner.scan_iq_samples = MagicMock(side_effect=lambda *args, **kwargs: ScanReport(
        scan_mode='offline_iq', start_freq_hz=2_400_000_000, stop_freq_hz=2_414_000_000,
        noise_floor_dbfs=-100, threshold_db=10, channels=[ChannelCandidate(
            frequency_hz=2_407_000_000, bandwidth_hz=350000, peak_power_dbfs=-20,
            snr_db=80, noise_floor_dbfs=-100, burst_count=1, active_slices=3,
            total_slices=20, duty_cycle=.15, details={'frequency_resolution_hz': 5000})]))


def live_scan(scanner, **kwargs):
    return scanner.scan_live_sdr('usrp', start_freq=2_400_000_000, stop_freq=2_414_000_000,
                                 sample_rate=10_000_000, step_bandwidth=10_000_000,
                                 candidate_bit_rates=(325000,), **kwargs)


def test_live_sweeps_reverse_direction_and_merge_observations(monkeypatch):
    device, captures = install_device(monkeypatch)
    scanner = SpectrumScanner()
    install_coarse(scanner)
    report = live_scan(scanner, verify_flrc=False, passes=2)
    hops = _plan_hops(2_400_000_000, 2_414_000_000, 10_000_000)
    assert [center for center, _ in captures] == hops + list(reversed(hops))
    assert len(report.channels) == 1
    channel = report.channels[0]
    assert channel.observation_count == channel.burst_count == 4
    assert channel.active_slices == 12 and channel.duty_cycle == .15
    assert report.device_info['hops_completed'] == 4
    device.__exit__.assert_called_once()


def test_refinement_replaces_coarse_candidate_after_large_frequency_correction(monkeypatch):
    device, captures = install_device(monkeypatch)
    scanner = SpectrumScanner()
    install_coarse(scanner)
    monkeypatch.setattr('flrc_modem.scan.engine.verify_flrc_candidate', lambda *args, **kwargs: {
        'is_flrc': True, 'verification_status': 'crc', 'crc_valid_frames': 2,
        'burst_count': 2, 'frequency_hz': 2_407_080_000, 'frequency_method': 'crc_cfo',
        'sync_words': ['54455354'], 'bit_rate': 325000, 'flrc_confidence': 1., 'protocol_hint': 'FLRC'})
    report = live_scan(scanner, passes=1, refine_dwell=1.2)
    assert len(captures) == 3 and captures[-1][1]['duration_s'] == 1.2
    assert all(kw['settle_seconds'] == .01 and kw['discard_seconds'] == .002 for _, kw in captures)
    assert len(report.channels) == 1
    channel = report.channels[0]
    assert channel.frequency_hz == 2_407_080_000
    assert channel.crc_valid_frames == 2 and channel.burst_count == 4
    assert channel.observation_count == 3 and channel.active_slices == 6
    assert report.device_info['refined_candidates'] == 1
    device.__exit__.assert_called_once()


def test_cancel_refinement_releases_device(monkeypatch):
    device, captures = install_device(monkeypatch)
    scanner = SpectrumScanner()
    install_coarse(scanner)
    receive = device.receive.side_effect

    def cancel(sink, config, sample_count, **kwargs):
        result = receive(sink, config, sample_count, **kwargs)
        if kwargs['duration_s'] == 1.2:
            scanner.cancel()
        return result

    device.receive.side_effect = cancel
    with pytest.raises(ScanCancelled):
        live_scan(scanner, passes=1, refine_dwell=1.2)
    assert len(captures) == 3
    device.__exit__.assert_called_once()


def test_channel_merge_preserves_crc_frequency_and_separates_narrow_carriers():
    energy = ChannelCandidate(2_407_000_000, 350000, -10, 90, -100,
                              sync_words=['deadbeef'], verification_status='preamble')
    confirmed = ChannelCandidate(2_407_030_000, 350000, -30, 70, -100,
                                  is_flrc=True, verification_status='crc', crc_valid_frames=2,
                                  sync_words=['54455354'])
    merged = _merge_adjacent_channels([energy, confirmed])
    assert len(merged) == 1 and merged[0].frequency_hz == confirmed.frequency_hz
    assert merged[0].crc_valid_frames == merged[0].details['crc_valid_frames'] == 2
    assert merged[0].sync_words == ['54455354']
    narrow = [ChannelCandidate(2_407_000_000 + offset, 2000, -10, 90, -100)
              for offset in (0, 70000)]
    assert len(_merge_adjacent_channels(narrow)) == 2


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_api_and_command_registry_forward_scan_controls(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from flrc_modem.scan_cli import make_argument_parser
    from webui.backend.app.commands.registry import CommandRegistry
    from webui.backend.app.main import app

    report = ScanReport('live_sdr_usrp', 2_400_000_000, 2_483_500_000, -100, 10)
    scan = MagicMock(return_value=report)
    monkeypatch.setattr(SpectrumScanner, 'scan_live_sdr', scan)
    controls = dict(passes=3, refine_dwell=1.2, max_refine_candidates=4,
                    settle_seconds=.02, discard_seconds=.003)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/scan/quick', json={'sdr': 'usrp', **controls})
    assert response.status_code == 200
    for name, value in controls.items():
        assert scan.call_args.kwargs[name] == value
    argv = CommandRegistry().build_cli_argv('scan', {'sdr': 'usrp', **controls})
    parsed = make_argument_parser().parse_args(argv[4:])
    for name, value in controls.items():
        assert getattr(parsed, name) == value


def test_cancellation_interrupts_fft_batches():
    scanner = SpectrumScanner()
    callbacks = []

    def cancelled():
        callbacks.append(True)
        if len(callbacks) == 2:
            scanner.cancel()
        from flrc_modem.scan.cancellation import check_cancelled
        check_cancelled(scanner.cancel_event)

    with pytest.raises(ScanCancelled):
        compute_psd_waterfall(noise(400000), check_cancelled=cancelled)
    assert len(callbacks) == 2
