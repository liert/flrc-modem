"""Recording completion must describe durable output, including failure paths."""
import json
from pathlib import Path

import numpy as np
import pytest

from webui.backend.app.spectrum.manager import SpectrumManager
from webui.backend.app.spectrum.schemas import SpectrumStartRequest
from webui.backend.app.spectrum.session import SpectrumSession
from webui.backend.app.storage.db import Database


@pytest.fixture
def recording(tmp_path, monkeypatch):
    db = Database(tmp_path / 'recording.db')
    monkeypatch.setattr('webui.backend.app.spectrum.session.PROJECT_ROOT', tmp_path)
    monkeypatch.setattr('webui.backend.app.spectrum.session.get_db', lambda: db)
    session = SpectrumSession(SpectrumStartRequest(sdr='sim', sample_rate=2_000_000))
    # Exercise the recording engine without starting any RX/DSP threads.
    session.state = 'running'
    return session, db, tmp_path


class FailingSink:
    def __init__(self, stream, failure):
        self.stream = stream
        self.failure = failure
        self.close_called = False

    def write(self, data):
        if self.failure == 'write':
            raise OSError('injected write failure')
        return self.stream.write(data)

    def flush(self):
        if 'flush' in self.failure:
            raise OSError('injected flush failure')
        return self.stream.flush()

    def close(self):
        self.close_called = True
        self.stream.close()
        if 'close' in self.failure:
            raise OSError('injected close failure')


def finish_recording(session, ending):
    if ending == 'cleanup':
        session.stop()
    elif ending == 'manual':
        session.stop_recording()
    # Timed recording already finished in _write_record_chunk.
    return session.get_recording_status()


@pytest.mark.parametrize('ending', ['manual', 'timed', 'cleanup'])
@pytest.mark.parametrize('failure', ['write', 'flush', 'close', 'flush-close', 'metadata', 'insert_run', 'insert_artifact'])
def test_recording_failure_never_reports_success(recording, monkeypatch, caplog, ending, failure):
    session, db, root = recording
    initial = session.start_recording(duration_s=.1 if ending == 'timed' else None)
    sink = FailingSink(session._record_sink, failure)
    session._record_sink = sink

    def reject(*args, **kwargs):
        raise OSError(f'injected {failure} failure')

    if failure == 'metadata':
        monkeypatch.setattr(Path, 'write_text', reject)
    elif failure == 'insert_run':
        monkeypatch.setattr(db, 'insert_run', reject)
    elif failure == 'insert_artifact':
        # Fail after the run INSERT to verify the real SQLite transaction rolls back.
        monkeypatch.setattr(db, '_insert_artifact', reject)
    if ending == 'timed':
        session._record_start_time -= 1

    data = np.full(32, .5 + .25j, dtype='<c8').tobytes()
    session._write_record_chunk(data)
    status = finish_recording(session, ending)
    assert status['is_recording'] is False
    assert status['completed'] is False
    assert status['run_id'] is None
    assert 'injected' in status['error']
    if failure == 'flush-close':
        assert 'flush' in status['error'] and 'close' in status['error']
    assert sink.close_called and sink.stream.closed
    assert status['filepath'] == initial['filepath']
    path = root / status['filepath']
    assert path.exists(), 'Keep failed IQ files available for recovery'
    if failure != 'write':
        assert path.read_bytes() == data
    assert db.get_runs() == []
    assert db.get_artifacts() == []
    events = [entry.msg for entry in caplog.records
              if isinstance(entry.msg, dict) and entry.msg.get('event') == 'spectrum.recording_failed']
    assert len(events) == 1
    assert events[0]['session_id'] == session.session_id
    assert events[0]['filepath'] == status['filepath']
    assert session.stop_recording() == status, 'Repeated stops preserve the failure result'


@pytest.mark.parametrize('ending', ['manual', 'timed', 'cleanup'])
def test_recording_success_has_a_registered_run_and_artifact(recording, ending):
    session, db, root = recording
    session.start_recording(duration_s=.1 if ending == 'timed' else None)
    if ending == 'timed':
        session._record_start_time -= 1
    data = np.full(32, .5 + .25j, dtype='<c8').tobytes()
    session._write_record_chunk(data)
    status = finish_recording(session, ending)
    assert status['completed'] is True and status['error'] is None
    assert db.get_run(status['run_id'])['status'] == 'SUCCEEDED'
    assert len(db.get_artifacts(status['run_id'])) == 1
    path = root / status['filepath']
    assert path.read_bytes() == data
    metadata = json.loads(Path(str(path) + '.json').read_text(encoding='utf-8'))
    assert metadata['samples_count'] == 32
    assert metadata['iq_format'] == 'cf32'
    assert session.stop_recording() == status


def test_stopped_session_retains_failed_recording_status(recording, monkeypatch):
    session, _, _ = recording
    manager = SpectrumManager()
    manager._active_session = session
    session.start_recording(duration_s=None)
    session._record_sink = FailingSink(session._record_sink, 'flush')
    manager.stop_session(session.session_id)
    status = manager.get_recording_status(session.session_id)
    assert status['completed'] is False
    assert 'flush' in status['error']
    assert manager.get_recording_status('other-session') == {'is_recording': False, 'completed': False}
