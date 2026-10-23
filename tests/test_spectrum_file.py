"""Real IQ file replay through the same FFT and demodulation pipeline as SDR input."""
import json
from pathlib import Path
import time

import numpy as np
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.testclient import TestClient

from flrc_modem.io.iq import decode_samples, encode_samples
from webui.backend.app.spectrum import files
from webui.backend.app.spectrum.manager import SpectrumManager
from webui.backend.app.spectrum.schemas import SpectrumStartRequest
from webui.backend.app.spectrum.session import SpectrumSession


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import webui.backend.app.artifacts.manager as artifacts
    output = tmp_path / 'outputs'
    output.mkdir()
    monkeypatch.setattr(files, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(artifacts, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(artifacts, 'ALLOWED_DIRS', [output])
    def no_hardware():
        raise AssertionError('File input must not acquire or probe hardware')
    monkeypatch.setattr('webui.backend.app.spectrum.session.get_device_manager', no_hardware)
    return output


def wait_until(condition, timeout=8):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError('file replay timed out')
        time.sleep(.01)


def tone_file(directory, fmt='cf32', count=32768):
    iq = (.5 * np.exp(2j * np.pi * .0625 * np.arange(count))).astype(np.complex64)
    path = directory / f'tone.{fmt}'
    path.write_bytes(encode_samples(iq, fmt))
    return path


@pytest.mark.parametrize('fmt', ['cs8', 'cu8', 'cf32'])
def test_file_fft_loop_pause_resume_and_release(workspace, fmt):
    path = tone_file(workspace, fmt)
    session = SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(path), iq_format=fmt,
                              sample_rate=2_000_000, center_freq=2_480_000_000, fft_average=1))
    try:
        session.start()
        wait_until(lambda: session._latest_frame is not None)
        first = session._latest_frame
        assert first['peak_freq'] == pytest.approx(2_480_125_000, abs=first['rbw_hz'])
        assert first['peak_power'] == pytest.approx(-6.02, abs=.2)
        assert session.get_status().device_type == 'file'
        assert session.get_status().device_name == path.name
        wait_until(lambda: session._latest_frame['sequence'] > first['sequence'])
        assert session.state == 'running'  # EOF rewinds instead of reporting a stream failure.
        session.pause_display()
        assert session.state == 'paused'
        session.resume_display()
        assert session.state == 'running'
    finally:
        session.stop()
    assert session._file_source.stream is None
    assert all(not thread.is_alive() for thread in (session._rx_thread, session._dsp_thread, session._demod_thread))


@pytest.mark.parametrize('name,content,fmt', [
    ('empty.cs8', b'', 'cs8'),
    ('partial.cs8', b'\0' * 2049, 'cs8'),
    ('short.cs8', b'\0' * 32, 'cs8'),
    ('nonfinite.cf32', np.full(2048, np.nan, dtype='<f4').tobytes(), 'cf32'),
], ids=['empty', 'partial', 'short', 'nonfinite'])
def test_invalid_file_does_not_stop_existing_session(workspace, name, content, fmt):
    manager = SpectrumManager()
    valid = tone_file(workspace)
    manager.start_session(SpectrumStartRequest(sdr='file', iq_path=str(valid), iq_format='cf32'))
    original = manager.get_session()
    bad = workspace / name
    bad.write_bytes(content)
    try:
        with pytest.raises(ValueError):
            manager.start_session(SpectrumStartRequest(sdr='file', iq_path=str(bad), iq_format=fmt))
        assert manager.get_session() is original
        assert original.state == 'running'
    finally:
        manager.stop_session()


def test_file_path_validation(workspace):
    with pytest.raises(ValidationError, match='选择 IQ 文件'):
        SpectrumStartRequest(sdr='file')
    with pytest.raises(PermissionError):
        SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(workspace.parent / 'private.cs8')))
    with pytest.raises(ValueError, match='不存在'):
        SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(workspace / 'missing.cs8')))


def test_file_bursts_are_visible_and_loop_boundaries_do_not_mark_display_gaps(workspace):
    iq = np.zeros(32768, dtype=np.complex64)
    iq[:8192] = .5 * np.exp(2j * np.pi * .0625 * np.arange(8192))
    path = workspace / 'burst.cf32'
    path.write_bytes(encode_samples(iq, 'cf32'))
    session = SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(path), iq_format='cf32', sample_rate=2_000_000))
    try:
        session.start()
        wait_until(lambda: session._sequence >= 2)
        assert session._latest_frame['peak_power'] == pytest.approx(-6.02, abs=.2)
        assert session._latest_frame['discontinuity'] is False
    finally:
        session.stop()


def test_recorded_file_replay_uses_the_actual_float_format(workspace, monkeypatch):
    from unittest.mock import Mock
    db = Mock()
    monkeypatch.setattr('webui.backend.app.spectrum.session.PROJECT_ROOT', workspace.parent)
    monkeypatch.setattr('webui.backend.app.spectrum.session.get_db', lambda: db)
    path = tone_file(workspace, 'cu8')
    session = SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(path), iq_format='cu8', sample_rate=2_000_000))
    try:
        session.start()
        session.start_recording(duration_s=None)
        wait_until(lambda: session.get_recording_status()['recorded_bytes'] > 0)
        status = session.stop_recording()
        assert status['iq_format'] == 'cf32'
        recorded = workspace.parent / status['filepath']
        assert recorded.suffix == '.cf32'
        assert status['samples_count'] == recorded.stat().st_size // 8
        samples = decode_samples(recorded.read_bytes(), 'cf32')
        assert np.max(np.abs(samples)) == pytest.approx(.5, abs=.02)
        assert json.loads(Path(str(recorded) + '.json').read_text())['iq_format'] == 'cf32'
        assert db.insert_artifact.call_args.kwargs['metadata']['iq_format'] == 'cf32'
    finally:
        session.stop()


def test_short_file_tail_demodulates_and_repeats_without_cross_loop_packets(workspace):
    from flrc_modem.phy.encoder import encode_packet
    from flrc_modem.modulation import ModulationConfig, iter_signal
    fixture = json.loads((Path(__file__).parent / 'fixtures/dk32s_packet.json').read_text())
    packet = encode_packet(bytes.fromhex(fixture['payload_hex']), bytes.fromhex(fixture['sync_hex']))
    iq = np.concatenate(list(iter_signal(packet.bits, ModulationConfig(sample_rate=2_000_000, repeat=1, guard_seconds=.002))))
    assert len(iq) < 2_000_000 * .05  # shorter than one live decode window
    path = workspace / 'packet.cs8'
    path.write_bytes(encode_samples(iq, 'cs8'))
    session = SpectrumSession(SpectrumStartRequest(sdr='file', iq_path=str(path), sample_rate=2_000_000,
                                                  demod_enabled=True, protocol='dk32s'))
    packets = []
    session._publish_packet = packets.append
    try:
        session.start()
        wait_until(lambda: len(packets) >= 2, timeout=20)
        assert all(record['crc_ok'] and record['payload_hex'].lower() == fixture['payload_hex'].lower() for record in packets)
        assert packets[-1]['time_s'] > packets[0]['time_s']
    finally:
        session.stop()


def test_upload_discovery_and_companion_metadata(workspace):
    app = FastAPI()
    app.include_router(files.router)
    with TestClient(app) as client:
        response = client.post('/api/v1/spectrum/files?filename=packet.cf32', content=b'\0' * 8192,
                               headers={'Content-Type': 'application/octet-stream'})
        assert response.status_code == 200
        uploaded = response.json()
        assert uploaded['iq_format'] == 'cf32'
        assert client.post('/api/v1/spectrum/files?filename=UPPER.CF32', content=b'\0' * 8192).json()['iq_format'] == 'cf32'
        path = workspace.parent / uploaded['path']
        assert path.read_bytes() == b'\0' * 8192
        Path(str(path) + '.json').write_text(json.dumps({'sample_rate': 2_500_000, 'center_frequency': 2_465_000_000, 'iq_format': 'cf32'}))
        listed = next(item for item in client.get('/api/v1/spectrum/files').json() if item['path'] == uploaded['path'])
        assert listed['sample_rate'] == 2_500_000
        assert listed['center_freq'] == 2_465_000_000
        Path(str(path) + '.json').write_text(json.dumps({'config': {'sample_rate': 10_000_000, 'iq_format': 'cu8'}}))
        listed = next(item for item in client.get('/api/v1/spectrum/files').json() if item['path'] == uploaded['path'])
        assert listed['sample_rate'] == 10_000_000
        assert listed['iq_format'] == 'cu8'
        Path(str(path) + '.json').write_text(json.dumps({'config': [], 'iq_format': {}}))
        assert client.get('/api/v1/spectrum/files').status_code == 200
        assert client.post('/api/v1/spectrum/files?filename=empty.cs8', content=b'').status_code == 400
        assert client.post('/api/v1/spectrum/files?filename=bad.exe', content=b'abc').status_code == 400
        assert not list(workspace.rglob('*.part'))


def test_oversized_upload_cleans_partial_file(workspace, monkeypatch):
    monkeypatch.setattr(files, 'MAX_UPLOAD_BYTES', 4)
    app = FastAPI()
    app.include_router(files.router)
    with TestClient(app) as client:
        assert client.post('/api/v1/spectrum/files?filename=large.cs8', content=b'123456').status_code == 413
    assert not list(workspace.rglob('*.*'))
