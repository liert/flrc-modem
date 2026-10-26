"""Raw selection fidelity, bounded retention, error paths and real offline decoding; no radio."""
import asyncio
import json
from pathlib import Path
import threading
import time

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from flrc_modem.io.iq import encode_samples, decode_samples
from webui.backend.app.spectrum.history import IQHistory, IQHistoryError
from webui.backend.app.spectrum.manager import SpectrumManager
from webui.backend.app.spectrum.schemas import SpectrumSelectionRequest, SpectrumStartRequest
from webui.backend.app.spectrum.session import SpectrumSession


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import webui.backend.app.artifacts.manager as artifacts
    monkeypatch.setattr(artifacts, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(artifacts, 'ALLOWED_DIRS', [tmp_path / 'outputs'])
    monkeypatch.setattr('webui.backend.app.spectrum.session.PROJECT_ROOT', tmp_path)
    monkeypatch.setattr('webui.backend.app.spectrum.files.PROJECT_ROOT', tmp_path)
    return tmp_path


def request(start=10.25, end=11.75, **values):
    return SpectrumSelectionRequest(time_start=start, time_end=end, **{
        'sample_rate': 100, 'center_frequency': 2480000000, 'target_frequency': 2480000001, **values})


@pytest.mark.parametrize('sample_bytes', [2, 8])
def test_sample_exact_cross_block_export_and_snapshot_survives_eviction(sample_bytes):
    history = IQHistory(sample_bytes, max_bytes=400 * sample_bytes)
    raw = np.arange(300 * sample_bytes, dtype=np.uint8).tobytes()
    for i in range(3):
        history.append(raw[i * 100 * sample_bytes:(i + 1) * 100 * sample_bytes], 10 + i, 100, 2480000000)
    snapshot = history.snapshot(10.25, 11.75, sample_rate=100, center_freq=2480000000)
    assert b''.join(snapshot.parts) == raw[25 * sample_bytes:175 * sample_bytes]
    assert snapshot.samples_count == 150
    history.append(b'\xff' * 400 * sample_bytes, 20, 100, 2480000000)
    assert b''.join(snapshot.parts) == raw[25 * sample_bytes:175 * sample_bytes]
    assert history.status()['bytes'] <= 400 * sample_bytes


def test_history_is_bounded_by_bytes_time_and_block_count():
    history = IQHistory(2, max_bytes=16, max_seconds=2, max_blocks=3)
    history.append(bytes(range(24)), 10, 4, 100)
    assert history.status()['bytes'] == 16
    assert history.status()['time_start'] == 11
    for i in range(20):
        history.append(b'xx', 20 + i / 4, 4, 100)
    assert history.status()['bytes'] == 6  # Metadata is bounded even for tiny input blocks.
    history = IQHistory(2, max_seconds=1)
    history.append(b'x' * 40, 10, 10, 100)
    assert history.status()['time_end'] - history.status()['time_start'] <= 1
    assert history.status()['bytes'] == 20


@pytest.mark.parametrize('kind', ['gap_flag', 'missing_samples', 'overlap', 'retune', 'rate_change'])
def test_discontinuous_or_mixed_iq_cannot_be_relabelled_as_continuous(kind):
    history = IQHistory(2)
    history.append(b'x' * 200, 10, 100, 2480000000)
    history.append(b'y' * 200, 11.1 if kind == 'missing_samples' else 10.9 if kind == 'overlap' else 11,
                   200 if kind == 'rate_change' else 100,
                   2480000010 if kind == 'retune' else 2480000000, kind == 'gap_flag')
    with pytest.raises(IQHistoryError) as caught:
        history.snapshot(10.5, 11.4, sample_rate=100, center_freq=2480000000)
    assert caught.value.code == ('configuration_changed' if kind in ('retune', 'rate_change') else 'history_gap')


def test_expired_empty_future_and_subsample_selections_report_clear_errors():
    history = IQHistory(2)
    with pytest.raises(IQHistoryError, match='尚无'):
        history.snapshot(10, 11, sample_rate=100, center_freq=100)
    history.append(b'x' * 200, 10, 100, 100)
    for start, end, code in [(9, 10.5, 'history_expired'), (10, 12, 'history_unavailable'),
                             (10, 10, 'invalid_selection'), (10, 10.00001, 'history_gap')]:
        with pytest.raises(IQHistoryError) as caught:
            history.snapshot(start, end, sample_rate=100, center_freq=100)
        assert caught.value.code == code


def test_backwards_clock_replaces_ambiguous_history_and_mutable_input_is_copied():
    history = IQHistory(2)
    data = bytearray(b'abcd')
    history.append(data, 10, 100, 100)
    data[:] = b'zzzz'
    assert b''.join(history.snapshot(10, 10.02, sample_rate=100, center_freq=100).parts) == b'abcd'
    history.append(b'efgh', 1, 100, 100)
    assert history.status()['time_start'] == 1
    assert history.status()['bytes'] == 4


@pytest.mark.parametrize('sdr,fmt,sample_bytes', [('hackrf', 'cs8', 2), ('usrp', 'cf32', 8)])
def test_session_export_is_sample_exact_discoverable_and_logs_success_and_failure(workspace, caplog, sdr, fmt, sample_bytes):
    manager = SpectrumManager()
    session = SpectrumSession(SpectrumStartRequest(sdr=sdr))
    manager._active_session = session
    raw = np.arange(200 * sample_bytes, dtype=np.uint8).tobytes()
    session.iq_history.append(raw, 10, 100, 2480000000)
    with caplog.at_level('INFO'):
        result = manager.export_selection(session.session_id, request())
        with pytest.raises(IQHistoryError):
            manager.export_selection(session.session_id, request(8, 9))
    path = workspace / result['filepath']
    assert result['iq_format'] == fmt
    assert path.read_bytes() == raw[25 * sample_bytes:175 * sample_bytes]
    metadata = json.loads(Path(str(path) + '.json').read_text(encoding='utf-8'))
    assert metadata['center_frequency'] == 2480000000
    assert metadata['target_frequency'] == 2480000001
    assert metadata['samples_count'] == 150
    assert metadata['duration_s'] == 1.5
    from webui.backend.app.spectrum.files import file_info
    assert file_info(path)['iq_format'] == fmt
    events = [entry.msg for entry in caplog.records if isinstance(entry.msg, dict)
              and entry.msg.get('event') == 'spectrum.selection_export']
    assert len(events) == 2
    assert events[0]['bytes'] == 150 * sample_bytes
    assert events[0]['outcome'] == 'success'
    assert events[1]['error_code'] == 'history_expired'
    assert all(event['request_id'] and event['duration_ms'] >= 0 for event in events)


def test_export_failure_removes_both_partial_files_and_releases_export_slot(workspace, monkeypatch):
    manager = SpectrumManager()
    session = SpectrumSession(SpectrumStartRequest(sdr='sim'))
    manager._active_session = session
    session.iq_history.append(b'x' * 1600, 10, 100, 2480000000)
    original = Path.write_text
    def fail_metadata(path, *args, **kwargs):
        if str(path).endswith('.json.part'):
            raise OSError('disk full')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_metadata)
    with pytest.raises(OSError, match='disk full'):
        manager.export_selection(session.session_id, request())
    assert not list((workspace / 'outputs' / 'captures').iterdir())
    monkeypatch.setattr(Path, 'write_text', original)
    assert manager.export_selection(session.session_id, request())['samples_count'] == 150


def test_stopped_session_history_remains_exportable_but_new_capture_invalidates_old_id(workspace):
    manager = SpectrumManager()
    session = SpectrumSession(SpectrumStartRequest(sdr='sim'))
    manager._active_session = session
    session.iq_history.append(b'x' * 1600, 10, 100, 2480000000)
    manager.stop_session(session.session_id)
    assert manager.export_selection(session.session_id, request())['samples_count'] == 150
    try:
        manager.start_session(SpectrumStartRequest(sdr='sim'))
        with pytest.raises(KeyError):
            manager.export_selection(session.session_id, request())
    finally:
        asyncio.run(manager.shutdown())
    assert manager._history_session is None


def test_host_callback_jitter_does_not_create_iq_gaps_but_explicit_loss_does():
    session = SpectrumSession(SpectrumStartRequest(sdr='hackrf', sample_rate=2000000))
    session._enqueue_iq(b'x' * 2000, 10)
    session._enqueue_iq(b'x' * 2000, 10.0007)  # Host scheduling jitter, continuous samples.
    assert session.iq_history.status()['time_end'] == pytest.approx(10.001)
    assert session.iq_history.snapshot(10, 10.001, sample_rate=2000000, center_freq=session.actual_center_freq).samples_count == 2000
    session._enqueue_iq(b'x' * 2000, 10.001, gap=True)
    with pytest.raises(IQHistoryError, match='接收中断'):
        session.iq_history.snapshot(10, 10.0015, sample_rate=2000000, center_freq=session.actual_center_freq)


@pytest.mark.parametrize('fmt', ['cs8', 'cu8', 'cf32'])
def test_file_replay_selection_uses_normalized_iq_while_paused_and_after_stop(workspace, fmt):
    rate, center = 2_000_000, 2_480_000_000
    iq = (.4 * np.exp(2j * np.pi * .0625 * np.arange(32768))).astype(np.complex64)
    path = workspace / 'outputs' / f'source.{fmt}'
    path.parent.mkdir(exist_ok=True)
    raw = encode_samples(iq, fmt)
    path.write_bytes(raw)
    normalized = encode_samples(decode_samples(raw, fmt), 'cf32')
    manager = SpectrumManager()
    try:
        manager.start_session(SpectrumStartRequest(sdr='file', iq_path=str(path), iq_format=fmt,
                                                   sample_rate=rate, center_freq=center))
        session = manager.get_session()
        deadline = time.monotonic() + 3
        while session.iq_history.status()['time_end'] - session.iq_history.status()['time_start'] < .03:
            assert time.monotonic() < deadline
            time.sleep(.005)
        origin = session.iq_history.status()['time_start']
        req = request(origin + .012, origin + .024, sample_rate=rate, center_frequency=center)
        manager.pause_session(session.session_id)
        result = manager.export_selection(session.session_id, req)
        assert result['iq_format'] == 'cf32'
        first = round((result['time_start'] - origin) * rate)
        expected = (normalized * 2)[first * 8:(first + result['samples_count']) * 8]
        assert (workspace / result['filepath']).read_bytes() == expected
        assert session.get_status().iq_history.bytes > 0
        manager.stop_session(session.session_id)
        stopped = manager.export_selection(session.session_id, req)
        assert (workspace / stopped['filepath']).read_bytes() == expected
    finally:
        asyncio.run(manager.shutdown())


def test_selection_api_validation_errors_and_disk_io_does_not_block_event_loop(workspace, monkeypatch):
    from webui.backend.app.api.router import api_router
    manager = SpectrumManager()
    session = SpectrumSession(SpectrumStartRequest(sdr='sim'))
    manager._active_session = session
    session.iq_history.append(b'x' * 1600, 10, 100, 2480000000)
    monkeypatch.setattr('webui.backend.app.api.router.get_spectrum_manager', lambda: manager)
    app = FastAPI()
    app.include_router(api_router)
    endpoint = f'/api/v1/spectrum/selection?session_id={session.session_id}'
    with TestClient(app) as client:
        assert client.post(endpoint, json=request().model_dump()).status_code == 200
        assert client.post(endpoint, json=request(8, 9).model_dump()).status_code == 409
        assert client.post(endpoint, json={**request().model_dump(), 'sample_rate': -1}).status_code == 422
        assert client.post(endpoint, json={**request().model_dump(), 'time_end': 10}).status_code == 422
        assert client.post(endpoint, json={**request().model_dump(), 'target_frequency': 2480000100}).status_code == 422
        assert client.post('/api/v1/spectrum/selection?session_id=missing', json=request().model_dump()).status_code == 404
    entered, release = threading.Event(), threading.Event()
    original = session.export_selection
    def slow_export(req):
        entered.set()
        assert release.wait(3)
        return original(req)
    monkeypatch.setattr(session, 'export_selection', slow_export)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            pending = asyncio.create_task(client.post(endpoint, json=request().model_dump()))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert (await asyncio.wait_for(client.get('/api/v1/spectrum/status'), 1)).status_code == 200
                assert (await client.post(endpoint, json=request().model_dump())).status_code == 409
                # The RX history remains usable throughout the disk export.
                session.iq_history.append(b'y' * 800, 12, 100, 2480000000)
            finally:
                release.set()
                assert (await pending).status_code == 200
    asyncio.run(exercise())


@pytest.mark.parametrize('fmt', ['cs8', 'cf32'])
def test_exported_nonzero_frequency_burst_runs_real_offline_demod(workspace, fmt):
    from flrc_modem.phy.encoder import encode_packet
    from flrc_modem.modulation import ModulationConfig, iter_signal
    from flrc_modem.cli import main
    from webui.backend.app.commands.registry import CommandRegistry
    fixture = json.loads((Path(__file__).parent / 'fixtures/dk32s_packet.json').read_text())
    packet = encode_packet(bytes.fromhex(fixture['payload_hex']), bytes.fromhex(fixture['sync_hex']))
    rate, center, offset = 2_000_000, 2_480_000_000, 100_000
    iq = np.concatenate(list(iter_signal(packet.bits, ModulationConfig(sample_rate=rate, repeat=1, guard_seconds=.002))))
    iq = (iq * np.exp(2j * np.pi * offset * np.arange(len(iq)) / rate)).astype(np.complex64)
    raw = encode_samples(iq, fmt)
    sample_bytes = 2 if fmt == 'cs8' else 8
    session = SpectrumSession(SpectrumStartRequest(sdr='hackrf' if fmt == 'cs8' else 'sim', sample_rate=rate))
    # Put an actual burst across several history blocks, rather than a reconstructed FFT.
    for first in range(0, len(iq), 2048):
        session.iq_history.append(raw[first * sample_bytes:(first + 2048) * sample_bytes],
                                  100 + first / rate, rate, center)
    result = session.export_selection(request(100, 100 + len(iq) / rate,
                                      sample_rate=rate, center_frequency=center, target_frequency=center + offset))
    frames_path = workspace / 'outputs' / f'frames_{fmt}.jsonl'
    argv = CommandRegistry().build_cli_argv('demod', {
        'iq': str(workspace / result['filepath']), 'iq_format': result['iq_format'],
        'sample_rate': result['sample_rate'], 'center_frequency': result['center_frequency'],
        'frequency': result['target_frequency'], 'sync': fixture['sync_hex'],
        'protocol': 'dk32s', 'output': str(frames_path), 'quiet': True,
    }, enable_ui_events=False)
    assert main(argv[4:]) == 0
    records = [json.loads(line) for line in frames_path.read_text(encoding='utf-8').splitlines()]
    assert records and all(record['crc_ok'] for record in records)
    assert records[0]['payload_hex'].lower() == fixture['payload_hex'].lower()
