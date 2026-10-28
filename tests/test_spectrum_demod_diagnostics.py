"""Demodulation failures stay visible and recover without reopening SDR hardware."""
import threading
import time
import base64
import json
from pathlib import Path

import numpy as np

from flrc_modem.pipeline import WindowDecoder
from webui.backend.app.spectrum.schemas import SpectrumStartRequest
from webui.backend.app.spectrum.session import SpectrumSession


def wait_until(condition, timeout=20):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= end:
            raise AssertionError('demod worker timed out')
        time.sleep(.01)


def start_worker(session):
    worker = threading.Thread(target=session._demod_worker, daemon=True)
    worker.start()
    return worker


def stop_worker(session, worker):
    session._cancel_event.set()
    worker.join(timeout=3)
    assert not worker.is_alive()


def test_pipeline_error_is_reported_once_and_capture_stays_running(monkeypatch):
    session = SpectrumSession(SpectrumStartRequest(sdr='usrp', demod_enabled=True))
    session.state = 'running'
    calls = []

    def fail():
        calls.append(True)
        raise ValueError('invalid live configuration')

    monkeypatch.setattr(session, '_build_demod_pipeline', fail)
    worker = start_worker(session)
    try:
        wait_until(lambda: session.demod_error_message is not None)
        status = session.get_status()
        assert status.state == 'running'
        assert status.demod_enabled is False
        assert status.demod_error_message == 'ValueError: invalid live configuration'
        assert calls == [True]
        assert worker.is_alive()
    finally:
        stop_worker(session, worker)


def test_window_failure_can_be_reenabled_and_reports_progress(monkeypatch):
    session = SpectrumSession(SpectrumStartRequest(sdr='usrp', sample_rate=2_000_000,
        demod_enabled=True, physical_template='physical/dk32s.toml'))
    session.state = 'running'
    calls = []

    def decode(decoder, iq, start):
        calls.append(start)
        decoder.stats.candidate_groups += 1
        if len(calls) == 1:
            raise ValueError('bad IQ window')
        session._cancel_event.set()
        return ()

    monkeypatch.setattr(WindowDecoder, 'decode', decode)
    raw = np.zeros(300_000, dtype=np.complex64).tobytes()
    session._demod_queue.put_latest(raw, 10.0)
    worker = start_worker(session)
    try:
        wait_until(lambda: session.demod_error_message is not None)
        assert session.get_status().demod_error_message == 'ValueError: bad IQ window'
        assert not session.demod_enabled
        session._apply_demod_request(session.request.model_copy(update={'demod_enabled': True}),
                                     session._demod_template_values)
        session._demod_queue.put_latest(raw, 20.0, True)
        wait_until(lambda: len(calls) == 2)
        worker.join(timeout=3)
        status = session.get_status()
        assert status.demod_error_message is None
        assert status.demod_windows_count == 2
        assert status.demod_candidates_count == 2
        assert status.demod_packets_count == 0
        assert calls == [10.0, 20.0]
        assert status.state == 'running'
    finally:
        stop_worker(session, worker)


def test_unexpected_worker_error_does_not_leave_a_dead_enabled_channel(monkeypatch):
    session = SpectrumSession(SpectrumStartRequest(sdr='usrp', demod_enabled=True))
    calls = []

    def process():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('window assembly failed')
        session._cancel_event.set()

    monkeypatch.setattr(session, '_process_demod', process)
    worker = start_worker(session)
    try:
        worker.join(timeout=3)
        assert calls == [True, True]
        assert session.demod_enabled is False
        assert session.get_status().demod_error_message == 'RuntimeError: window assembly failed'
    finally:
        stop_worker(session, worker)


def test_recorded_ble_is_identified_without_stopping_flrc_and_clears_on_recovery():
    from flrc_modem.io.iq import decode_samples, encode_samples
    from flrc_modem.modulation import ModulationConfig, iter_signal
    from flrc_modem.phy.encoder import encode_packet
    fixture = json.loads((Path(__file__).parent / 'fixtures/ble_advertisement.json').read_text())
    iq = np.zeros(480_000, dtype=np.complex64)
    recorded = decode_samples(base64.b64decode(fixture['iq_base64']), 'cs8')
    iq[160_000:160_000 + len(recorded)] = recorded
    session = SpectrumSession(SpectrumStartRequest(sdr='hackrf', sample_rate=8_000_000,
                              demod_enabled=True))
    session.state = 'running'
    session._demod_queue.put_latest(encode_samples(iq, 'cs8'), 10.0)
    worker = start_worker(session)
    try:
        wait_until(lambda: session.demod_signal_hint is not None)
        status = session.get_status()
        assert 'BLE 1M' in status.demod_signal_hint and '信道 39' in status.demod_signal_hint
        assert status.demod_packets_count == 0 and status.demod_candidates_count == 0
        assert status.demod_enabled and status.demod_error_message is None
        assert status.state == 'running'
        # A real FLRC packet suppresses the hint even if BLE is also present.
        packet = encode_packet(bytes(range(20)), bytes.fromhex('b42b2bb4'))
        waveform = np.concatenate(list(iter_signal(packet.bits, ModulationConfig(
            sample_rate=8_000_000, bit_rate=325_000, coding_rate='3/4', bt=1.0))))
        iq[200_000:200_000 + len(waveform)] += waveform
        session._demod_queue.put_latest(encode_samples(iq, 'cs8'), 11.0, True)
        wait_until(lambda: session.demod_packets_count > 0)
        assert session.get_status().demod_signal_hint is None
        assert session.demod_enabled and session.demod_error_message is None
    finally:
        stop_worker(session, worker)


def test_optional_signal_probe_cannot_disable_the_receiver(monkeypatch):
    session = SpectrumSession(SpectrumStartRequest(sdr='hackrf', demod_enabled=True))
    def fail(*args):
        raise ValueError('diagnostic failed')
    monkeypatch.setattr('webui.backend.app.spectrum.signal_diagnostics.detect_ble_advertisement', fail)
    _, _, config = session._build_demod_pipeline()
    session._probe_demod_signal(np.zeros(1000, np.complex64), config)
    assert session.demod_enabled and session.demod_error_message is None
    assert session.demod_signal_hint is None
