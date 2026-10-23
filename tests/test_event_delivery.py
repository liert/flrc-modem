"""Task output isolation: real pipes, slow storage/clients, bounded queues and failures."""
import asyncio
import json
import sys
import threading

import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.events.hub import EventHub
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.main import app
from webui.backend.app.storage.db import Database


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def output(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.events import hub as hub_module
    database = Database(tmp_path / 'output.db')
    hub = EventHub(batch_delay=0)
    monkeypatch.setattr(db_module, '_DB', database)
    monkeypatch.setattr(hub_module, '_EVENT_HUB', hub)
    return database, hub


class Socket:
    def __init__(self, blocked=False):
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        self.messages = []
        self.code = None

    async def accept(self):
        pass

    async def send_text(self, text):
        self.entered.set()
        await self.release.wait()
        self.messages.append(text)

    async def close(self, code=1000):
        self.code = code
        self.closed.set()


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(poll(), 5)


@pytest.mark.anyio
async def test_real_pipe_is_drained_while_database_write_is_blocked(output, monkeypatch):
    database, hub = output
    database.insert_run({'id': 'run', 'command_id': 'demod', 'command_args': {},
                         'cli_argv': [], 'status': 'RUNNING', 'created_at': 'now'})
    entered, release = threading.Event(), threading.Event()
    original = database.insert_output_batch
    def blocked_write(batch):
        with database._lock:
            entered.set()
            assert release.wait(10)
        return original(batch)
    monkeypatch.setattr(database, 'insert_output_batch', blocked_write)
    child = await asyncio.create_subprocess_exec(sys.executable, '-u', '-c',
        "import sys; [print(str(i) + ':' + 'x'*200) for i in range(1000)]; print('stderr-tail', file=sys.stderr)",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    jobs = JobManager()
    readers = [asyncio.create_task(jobs._stream_reader('run', child.stdout, True)),
               asyncio.create_task(jobs._stream_reader('run', child.stderr, False))]
    client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')
    polling = None
    try:
        await until(entered.is_set)
        polling = asyncio.create_task(client.get('/api/v1/runs/run'))
        await asyncio.sleep(.01)
        # Storage is still blocked: both the child and its pipe readers must finish.
        assert await asyncio.wait_for(child.wait(), 5) == 0
        await asyncio.wait_for(asyncio.gather(*readers), 5)
        assert not release.is_set() and not polling.done()
        assert hub.snapshot()['queued_records'] > 0
    finally:
        release.set()
        if child.returncode is None:
            child.kill()
        await child.wait()
        await asyncio.gather(*readers, return_exceptions=True)
        await hub.shutdown()
        if polling is not None:
            assert (await asyncio.wait_for(polling, 5)).status_code == 200
        await client.aclose()
    logs = database.get_logs('run')
    assert [row['line'].split(':')[0] for row in logs if row['stream'] == 'stdout'] == [str(i) for i in range(1000)]
    assert [row['line'] for row in logs if row['stream'] == 'stderr'] == ['stderr-tail']
    assert hub.snapshot()['batches'] < 20


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['timeout', 'records', 'bytes'])
async def test_slow_client_cannot_delay_storage_or_another_client(output, failure):
    database, _ = output
    hub = EventHub(batch_delay=0, client_records=8, client_bytes=2048, send_timeout=.05)
    slow, fast = Socket(blocked=True), Socket()
    await hub.register(slow, 'run')
    await hub.register(fast)
    try:
        await hub.dispatch_log('run', 'stdout', 'first')
        await hub.flush()
        await slow.entered.wait()
        if failure == 'timeout':
            await hub.dispatch_event('run', 'duplex.result', 1, None, 'now', {'success': True})
        elif failure == 'records':
            # Give the fast sender time to consume each event; the blocked one fills.
            for i in range(12):
                await hub.dispatch_log('run', 'stdout', str(i))
                await hub.flush()
                await until(lambda: len(fast.messages) == i + 2)
        else:
            for i in range(3):
                await hub.dispatch_log('run', 'stdout', str(i) + 'x' * 900)
                await hub.flush()
                await until(lambda: len(fast.messages) == i + 2)
        await hub.flush()
        await asyncio.wait_for(slow.closed.wait(), 5)
        await hub.send_text(fast, 'pong')
        await until(lambda: fast.messages and fast.messages[-1] == 'pong')
        assert slow.code == 1013 and fast.code is None
        assert hub.snapshot()['slow_clients'] == 1
        assert len(database.get_logs('run')) == (13 if failure == 'records' else 4 if failure == 'bytes' else 1)
        if failure == 'timeout':
            assert json.loads(fast.messages[-2])['type'] == 'duplex.result'
            assert database.get_events('run')[0]['payload']['success'] is True
    finally:
        await hub.shutdown()
    assert not hub._clients and not hub._retiring


@pytest.mark.anyio
async def test_snapshots_coalesce_but_results_order_and_flush_boundaries_survive(output):
    database, hub = output
    for i in range(100):
        await hub.dispatch_event('run', 'metric.sample', i, 'dsp', 'now', {'count': i})
    await hub.dispatch_event('run', 'duplex.result', 100, None, 'now', {'success': True})
    for i in range(101, 201):
        await hub.dispatch_event('run', 'metric.sample', i, 'dsp', 'now', {'count': i})
    await hub.shutdown()
    events = database.get_events('run')
    assert [event['seq'] for event in events] == [99, 100, 200]
    assert hub.snapshot()['coalesced_snapshots'] == 198


@pytest.mark.anyio
async def test_overflow_before_sender_starts_still_closes_socket(output):
    _, hub = output
    hub._client_records = 1
    client = Socket()
    await hub.register(client)
    for i in range(3):
        await hub.notify_run_change('run', 'task.updated', {'i': i})
    await hub.shutdown()
    assert client.closed.is_set() and client.code == 1013
    assert not hub._clients and not hub._retiring


def test_output_batch_is_atomic_preserves_attempts_and_does_not_revive_deleted_runs(output):
    database, _ = output
    database.insert_run({'id': 'run', 'command_id': 'demod', 'command_args': {},
                         'cli_argv': [], 'status': 'SUCCEEDED', 'created_at': 'now'})
    log = {'type': 'task.log', 'run_id': 'run', 'stream': 'stdout', 'line': 'first', 'timestamp': 'now'}
    event = {'type': 'artifact.created', 'run_id': 'run', 'seq': 1, 'stage_id': 'output',
             'timestamp': 'now', 'payload': {'path': 'outputs/test.jsonl', 'metadata': {'invalid': object()}}}
    with pytest.raises(TypeError):
        database.insert_output_batch([log, event])
    assert database.get_logs('run') == database.get_events('run') == database.get_artifacts('run') == []
    event['payload']['metadata'] = {'size_bytes': 0}
    assert [item['attempt'] for item in database.insert_output_batch([log, event])] == [1, 1]
    record = database.get_run('run')
    record['attempt_created_at'] = 'later'
    database.restart_run(record)
    database.insert_output_batch([{**log, 'line': 'second'}, event])
    assert database.get_logs('run', attempt=1)[0]['line'] == 'first'
    assert database.get_logs('run', attempt=2)[0]['line'] == 'second'
    assert len(database.get_artifacts('run', attempt=1)) == len(database.get_artifacts('run', attempt=2)) == 1
    database.update_run_status('run', 'SUCCEEDED')
    database.delete_runs(['run'])
    assert database.insert_output_batch([log, event]) == []
    assert database.get_events('run') == database.get_logs('run') == []


@pytest.mark.anyio
async def test_full_storage_queue_has_bounded_backpressure_without_dropping_logs(output):
    database, _ = output
    hub = EventHub(max_records=2, max_bytes=400, batch_size=1, batch_delay=0)
    entered, release = threading.Event(), threading.Event()
    original = database.insert_output_batch
    def blocked_write(batch):
        entered.set()
        assert release.wait(10)
        return original(batch)
    database.insert_output_batch = blocked_write
    async def produce():
        for i in range(10):
            await hub.dispatch_log('run', 'stdout', str(i))
    producer = asyncio.create_task(produce())
    try:
        await until(entered.is_set)
        assert not producer.done()
        stats = hub.snapshot()
        assert stats['high_records'] <= 2 and stats['high_bytes'] <= 400
        assert stats['backpressure_count'] > 0
    finally:
        release.set()
        await asyncio.wait_for(producer, 5)
        await hub.shutdown()
    assert [row['line'] for row in database.get_logs('run')] == [str(i) for i in range(10)]


@pytest.mark.anyio
async def test_write_failure_stops_silent_child_and_marks_task_failed(output, monkeypatch):
    database, hub = output
    from webui.backend.app.commands.registry import get_command_registry
    from webui.backend.app.devices import manager as devices_module
    from webui.backend.app.devices.manager import DeviceManager
    devices = DeviceManager()
    monkeypatch.setattr(devices_module, '_DEVICE_MGR', devices)
    monkeypatch.setattr(get_command_registry(), 'build_cli_argv', lambda *args: [
        sys.executable, '-u', '-c', "import time; print('ready'); time.sleep(60)"])
    def fail(batch):
        raise OSError('simulated disk full')
    monkeypatch.setattr(database, 'insert_output_batch', fail)
    jobs = JobManager()
    record = await jobs.create_and_start_run('live', {})
    await asyncio.wait_for(jobs._tasks[record.id], 12)
    assert database.get_run(record.id)['status'] == 'FAILED'
    assert devices.get_lock_info('hackrf') is None and not jobs._active_processes
    assert 'disk full' in hub.snapshot()['write_error']
    with pytest.raises(RuntimeError, match='disk full'):
        await hub.flush()
    with pytest.raises(RuntimeError, match='disk full'):
        await hub.shutdown()
