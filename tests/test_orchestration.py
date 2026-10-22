"""Mixed scheduling, cancellation and persisted API records without RF hardware."""
import asyncio
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.sync.control import run_session
from flrc_modem.sync.requests import validate_request
from flrc_modem.sync.orchestration import PlanScheduler, validate_plan
from flrc_modem.sync.transceiver import TDDExchangeResult
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.storage.db import Database

HEX = '010203040506'


def plan(**overrides):
    return {'plan_id': 'mixed', 'periodic': [{'id': 'rc', 'interval_ms': 500, 'payload_hex': HEX}],
            'steps': [{'id': 'query', 'payload_hex': HEX, 'wait_reply': False}], **overrides}


def scheduler():
    clock, output = [100.0], io.StringIO()
    engine = PlanScheduler(UIEventEmitter(output, True), clock=lambda: clock[0])
    return engine, clock, output


def events(output, kind):
    return [event['payload'] for event in map(json.loads, output.getvalue().splitlines()) if event['type'] == kind]


def finish(engine, job, status='tx_completed'):
    engine.finish(job, {'status': status, 'tx_completed': status != 'send_failed', 'reply_matched': status == 'reply_matched'})


def test_plan_validation_defaults_and_defensive_copy():
    original = plan()
    normalized = validate_plan(original)
    assert normalized['periodic'][0]['wait_reply'] is False
    assert normalized['steps'][0]['delay_ms'] == 0
    assert normalized['steps'][0]['on_failure'] == 'stop'
    assert normalized['cycles'] == 1
    normalized['periodic'][0]['interval_ms'] = 1
    assert original['periodic'][0]['interval_ms'] == 500


def test_manual_requests_cannot_collide_with_generated_plan_ids():
    engine, _, _ = scheduler()
    engine.start(plan())
    job = engine.next_job()
    assert job.request_id.startswith('@plan:')
    with pytest.raises(ValueError, match='保留'):
        validate_request({'request_id': job.request_id, 'payload_hex': HEX})


@pytest.mark.parametrize('invalid', [
    {}, {'plan_id': 'empty'}, plan(cycles=True), plan(cycles=0), plan(unknown=True),
    plan(periodic=[{'id': 'x', 'interval_ms': 0, 'payload_hex': HEX}]),
    plan(periodic=[{'id': 'x', 'interval_ms': float('nan'), 'payload_hex': HEX}]),
    plan(periodic=[{'id': 'x', 'interval_ms': 100, 'count': -1, 'payload_hex': HEX}]),
    plan(periodic=[{'id': 'x', 'interval_ms': 100, 'payload_hex': 'bad'}]),
    plan(steps=[{'id': 'x', 'payload_hex': HEX, 'on_failure': 'retry'}]),
    plan(steps=[{'id': 'x', 'payload_hex': HEX}] * 2),
    plan(steps=[{'id': 'x', 'payload_hex': HEX, 'message': {}}]),
    plan(steps=[{'id': 'x', 'message': [], 'delay_ms': 0}]),
    plan(periodic=[{'id': 'x', 'interval_ms': 100, 'payload_hex': HEX, 'wait_reply': 'false'}]),
    plan(periodic=[{'id': str(i), 'interval_ms': 100, 'payload_hex': HEX} for i in range(9)]),
])
def test_reject_whole_malformed_plan(invalid):
    with pytest.raises((ValueError, TypeError)):
        validate_plan(invalid)


def test_mixed_workflow_delays_do_not_block_periodic_and_deadlines_do_not_drift():
    engine, clock, output = scheduler()
    engine.start(plan(steps=[{'id': 'query', 'payload_hex': HEX, 'delay_ms': 800, 'wait_reply': False}]))
    job = engine.next_job()
    assert job.metadata['source'] == 'periodic'
    clock[0] = 100.21
    finish(engine, job)
    assert engine.next_job() is None
    clock[0] = 100.5
    job = engine.next_job()
    assert job.metadata['scheduled_offset_ms'] == 500
    clock[0] = 100.71
    finish(engine, job)
    clock[0] = 100.8
    job = engine.next_job()
    assert job.metadata['source'] == 'step'
    assert job.metadata['cycle'] == 1
    clock[0] = 101.15
    finish(engine, job)
    job = engine.next_job()
    assert job.metadata['source'] == 'periodic'
    assert job.metadata['scheduled_offset_ms'] == 1000
    assert job.metadata['lateness_ms'] == 150
    clock[0] = 101.7
    finish(engine, job)
    assert engine.skipped == 1
    assert engine.next_job() is None
    assert engine.periodic[0]['due'] == 102
    assert events(output, 'duplex.result')[-1]['skipped_periods'] == 1


def test_slow_send_skips_missed_cycles_without_a_catchup_burst():
    engine, clock, output = scheduler()
    engine.start(plan(steps=[], periodic=[{'id': 'rc', 'payload_hex': HEX, 'interval_ms': 10, 'count': 2}]))
    job = engine.next_job()
    assert engine.next_job() is None  # One outstanding exchange, no overlapping TX.
    clock[0] += 1.005
    finish(engine, job)
    assert engine.skipped == 100
    assert engine.next_job() is None
    clock[0] = engine.periodic[0]['due']
    finish(engine, engine.next_job())
    assert engine.next_job() is None and engine.state == 'completed'
    assert len(events(output, 'duplex.result')) == 2


def test_flow_loops_are_sequential_and_apply_delay_before_each_iteration():
    engine, clock, _ = scheduler()
    engine.start(plan(periodic=[], cycles=2, steps=[
        {'id': 'a', 'payload_hex': HEX, 'delay_ms': 100}, {'id': 'b', 'payload_hex': HEX}]))
    assert engine.next_job() is None
    for cycle in (1, 2):
        clock[0] = engine.workflow_due
        first = engine.next_job()
        assert first.metadata['item_id'] == 'a' and first.metadata['cycle'] == cycle
        assert first.wait_reply is True
        finish(engine, first, 'reply_matched')
        second = engine.next_job()
        assert second.metadata['item_id'] == 'b'
        finish(engine, second, 'reply_matched')
        assert engine.next_job() is None
    assert engine.state == 'completed' and engine.executed == 4


@pytest.mark.parametrize('policy,state', [('stop', 'failed'), ('continue', 'running')])
def test_reply_timeout_obeys_failure_policy_without_automatic_retry(policy, state):
    engine, _, output = scheduler()
    engine.start(plan(steps=[{'id': 'query', 'payload_hex': HEX, 'on_failure': policy}]))
    job = engine.next_job()
    assert job.metadata['source'] == 'step'
    finish(engine, job, 'reply_timeout')
    assert engine.state == state and engine.failed == 1
    assert len(events(output, 'duplex.result')) == 1
    if policy == 'stop':
        assert engine.next_job() is None
    else:
        assert engine.next_job().metadata['source'] == 'periodic'


def test_stop_cancels_only_plan_and_blocks_replacement_until_current_exchange_finishes():
    engine, _, output = scheduler()
    engine.start(plan())
    job = engine.next_job()
    engine.stop('mixed')
    assert job.cancel.is_set() and engine.state == 'stopping'
    assert engine.next_job() is None
    with pytest.raises(ValueError, match='已有编排'):
        engine.start(plan(plan_id='another'))
    finish(engine, job, 'cancelled')
    assert engine.state == 'stopped'
    with pytest.raises(ValueError, match='已提交'):
        engine.start(plan())
    engine.start(plan(plan_id='another'))
    with pytest.raises(ValueError, match='未运行'):
        engine.stop('mixed')
    assert engine.state == 'running'
    engine.stop('another', interrupted=True)
    assert events(output, 'duplex.plan')[-1]['status'] == 'interrupted'


def test_execution_limit_stops_an_indefinite_plan(monkeypatch):
    monkeypatch.setattr('flrc_modem.sync.orchestration.MAX_EXECUTIONS', 2)
    engine, clock, _ = scheduler()
    engine.start(plan(steps=[]))
    for _ in range(2):
        finish(engine, engine.next_job())
        clock[0] += .5
    assert engine.next_job() is None and engine.state == 'limit_reached'


def test_real_session_controller_runs_both_lanes_and_manual_requests():
    trx = MagicMock()
    trx.sync_diagnostics.return_value = {'state': 'locked'}
    trx.send_and_wait_reply.return_value = TDDExchangeResult(True, b'123456', 1, 0, 2400000000, tx_completed=True)
    records = [
        {'action': 'start_plan', 'plan': plan(periodic=[{'id': 'rc', 'payload_hex': HEX, 'interval_ms': 1, 'count': 2}])},
        {'request_id': 'manual', 'payload_hex': HEX, 'wait_reply': False},
    ]
    output = io.StringIO()
    result = run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(output, True),
                         io.StringIO(''.join(json.dumps(record) + '\n' for record in records)))
    assert result == 0
    results = events(output, 'duplex.result')
    assert len(results) == 4
    assert len([r for r in results if r.get('source') == 'periodic']) == 2
    assert len([r for r in results if r.get('source') == 'step']) == 1
    assert len([r for r in results if r['request_id'] == 'manual']) == 1
    assert len(set(r['request_id'] for r in results)) == 4
    assert events(output, 'duplex.plan')[-1]['status'] == 'completed'


def test_stop_plan_does_not_stop_radio_or_send_queued_plan_jobs():
    trx, output = MagicMock(), io.StringIO()
    trx.sync_diagnostics.return_value = {'state': 'locked'}
    commands = [{'action': 'start_plan', 'plan': plan()}, {'action': 'stop_plan', 'plan_id': 'mixed'},
                {'request_id': 'after', 'payload_hex': HEX, 'wait_reply': False}]
    trx.send_and_wait_reply.return_value = TDDExchangeResult(True, b'123456', 1, 0, 2400000000, tx_completed=True)
    assert run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(output, True),
        io.StringIO(''.join(json.dumps(record) + '\n' for record in commands))) == 0
    trx.request_stop.assert_not_called()
    assert any(r['request_id'] == 'after' for r in events(output, 'duplex.result'))
    assert events(output, 'duplex.plan')[-1]['status'] == 'stopped'


def test_cli_rejects_late_invalid_payload_before_any_earlier_job_runs():
    trx, output = MagicMock(), io.StringIO()
    trx.sync_diagnostics.return_value = {'state': 'locked'}
    trx.session.sample_rate = 2500000
    definition = plan(steps=[{'id': 'bad', 'message': {'command': 'invalid'}}])
    stream = io.StringIO(json.dumps({'action': 'start_plan', 'plan': definition}) + '\n')
    assert run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(output, True), stream) == 0
    trx.send_and_wait_reply.assert_not_called()
    assert events(output, 'duplex.plan.rejected')[0]['plan_id'] == 'mixed'


def test_plan_api_validates_before_write_tracks_ids_and_survives_status_events(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webui.backend.app.main import app
    from webui.backend.app.jobs import manager as job_module
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.events.hub import EventHub

    database, manager = Database(tmp_path / 'plans.db'), JobManager()
    monkeypatch.setattr(db_module, '_DB', database)
    monkeypatch.setattr(job_module, '_JOB_MGR', manager)
    monkeypatch.setattr(hub_module, '_EVENT_HUB', EventHub())
    database.insert_run({'id': 'session', 'command_id': 'duplex', 'command_args': {'session': True},
                         'cli_argv': [], 'status': 'RUNNING', 'created_at': 'now'})

    async def scenario():
        stdin = MagicMock()
        async def drain():
            pass
        stdin.drain = drain
        proc = SimpleNamespace(returncode=None, stdin=stdin)
        manager._active_processes['session'] = proc
        state = {'ready': True, 'lock': asyncio.Lock(), 'pending': set(), 'seen': set(), 'sample_rate': 2500000}
        manager._session_inputs['session'] = state
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            path = '/api/v1/runs/session/plan'
            bad = plan(steps=[{'id': 'bad', 'message': {'direction': 'ground_to_air', 'command': 'invalid'}}])
            assert (await client.post(path, json={'action': 'start_plan', 'plan': bad})).status_code == 400
            stdin.write.assert_not_called()
            sent = await client.post(path, json={'action': 'start_plan', 'plan': plan()})
            assert sent.status_code == 200, sent.text
            assert json.loads(stdin.write.call_args.args[0])['plan']['plan_id'] == 'mixed'
            assert (await client.post(path, json={'action': 'start_plan', 'plan': plan(plan_id='other')})).status_code == 409
            assert (await client.post(path, json={'action': 'stop_plan', 'plan_id': 'stale'})).status_code == 409
            assert (await client.post(path, json={'action': 'stop_plan', 'plan_id': 'mixed'})).status_code == 200
            definition = {'type': 'duplex.plan.definition', 'seq': 1, 'payload': validate_plan(plan())}
            await manager._handle_line('session', json.dumps(definition).encode(), True)
            final = {'type': 'duplex.plan', 'seq': 2, 'payload': {'plan_id': 'mixed', 'status': 'stopped', 'executed': 2}}
            await manager._handle_line('session', json.dumps(final).encode(), True)
            assert state['active_plan'] is None
            for sequence in range(3, 200):
                database.insert_event('session', sequence, 'duplex.status', None, 'now', {'state': 'locked'})
            snapshot = (await client.get('/api/v1/runs/session/session')).json()
            assert snapshot['plan']['status'] == 'stopped' and snapshot['plan']['executed'] == 2
            assert snapshot['plan_definition']['steps'][0]['id'] == 'query'
            assert (await client.post(path, json={'action': 'start_plan', 'plan': plan()})).status_code == 409
            proc.returncode = 0
            assert (await client.post(path, json={'action': 'start_plan', 'plan': plan(plan_id='new')})).status_code == 409
    asyncio.run(scenario())


def test_mixed_plan_real_subprocess_api_stop_and_crash_recovery(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webui.backend.app.main import app
    from webui.backend.app.jobs import manager as job_module
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.devices import manager as device_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.events.hub import EventHub

    database, manager = Database(tmp_path / 'subprocess.db'), JobManager()
    monkeypatch.setattr(db_module, '_DB', database)
    monkeypatch.setattr(job_module, '_JOB_MGR', manager)
    monkeypatch.setattr(device_module, '_DEVICE_MGR', device_module.DeviceManager())
    monkeypatch.setattr(hub_module, '_EVENT_HUB', EventHub())
    spawn = asyncio.create_subprocess_exec
    script = '''
import time
from types import SimpleNamespace
from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.sync.control import run_session
from flrc_modem.sync.transceiver import TDDExchangeResult
class Radio:
 session=SimpleNamespace(sample_rate=2500000)
 def sync_diagnostics(self): return {'state':'locked'}
 def request_stop(self): pass
 def send_and_wait_reply(self,payload,wait_reply,cancel_event=None,**kwargs):
  if cancel_event is not None and cancel_event.wait(.02):
   return TDDExchangeResult(False,b'123456',0,0,2400000000,cancelled=True)
  return TDDExchangeResult(True,b'123456',time.monotonic(),0,2400000000,
                           tx_completed=True,reply_matched=wait_reply)
emitter=UIEventEmitter(enabled=True)
emitter.emit('duplex.ready',payload={'sample_rate':2500000,'device_args':'fake','channel':0})
raise SystemExit(run_session(Radio(),SimpleNamespace(timeout=1,sync_timeout=5),emitter))
'''
    async def fake_spawn(*argv, **kwargs):
        assert '--session' in argv
        assert kwargs['env']['PYTHONUTF8'] == '1'
        return await spawn(sys.executable, '-u', '-c', script, **kwargs)
    monkeypatch.setattr(job_module.asyncio, 'create_subprocess_exec', fake_spawn)

    async def wait_until(predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 5)

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/runs', json={'command_id': 'duplex', 'parameters': {'session': True}})
            assert response.status_code == 200, response.text
            ident = response.json()['id']
            path = f'/api/v1/runs/{ident}/plan'
            try:
                await wait_until(lambda: manager._session_inputs.get(ident, {}).get('ready'))
                definition = plan(periodic=[{'id': 'period', 'interval_ms': 40, 'count': 3, 'payload_hex': HEX}],
                                  steps=[{'id': '查询', 'payload_hex': HEX, 'delay_ms': 30, 'wait_reply': True}], cycles=2)
                response = await client.post(path, json={'action': 'start_plan', 'plan': definition})
                assert response.status_code == 200, response.text
                await wait_until(lambda: (database.get_message_state(ident)['plan'] or {}).get('status') == 'completed')
                snapshot = (await client.get(f'/api/v1/runs/{ident}/session')).json()
                assert snapshot['plan']['executed'] == snapshot['plan']['succeeded'] == 5
                assert snapshot['plan']['cycles_completed'] == 2
                assert len(snapshot['results']) == 5
                assert len([r for r in snapshot['results'] if r['source'] == 'periodic']) == 3
                assert len([r for r in snapshot['results'] if r['source'] == 'step' and r['reply_matched']]) == 2
                assert snapshot['plan_definition']['steps'][0]['id'] == '查询'

                continuous = plan(plan_id='continuous', steps=[])
                assert (await client.post(path, json={'action': 'start_plan', 'plan': continuous})).status_code == 200
                await wait_until(lambda: (database.get_message_state(ident)['plan'] or {}).get('plan_id') == 'continuous')
                assert (await client.post(path, json={'action': 'stop_plan', 'plan_id': 'continuous'})).status_code == 200
                await wait_until(lambda: database.get_message_state(ident)['plan']['status'] == 'stopped')
                assert database.get_run(ident)['status'] == 'RUNNING'
                assert (await client.post(f'/api/v1/runs/{ident}/messages', json={
                    'request_id': 'after-stop', 'payload_hex': HEX, 'wait_reply': False})).status_code == 200
                await wait_until(lambda: any(r['request_id'] == 'after-stop' for r in database.get_message_state(ident)['results']))

                assert (await client.post(path, json={'action': 'start_plan', 'plan': plan(plan_id='crash', steps=[])})).status_code == 200
                await wait_until(lambda: database.get_message_state(ident)['plan']['plan_id'] == 'crash')
                manager._active_processes[ident].kill()
                await wait_until(lambda: database.get_run(ident)['status'] == 'FAILED')
                snapshot = database.get_message_state(ident)
                assert snapshot['plan']['status'] == 'interrupted'
                assert snapshot['plan']['plan_id'] == 'crash'
                assert ident not in manager._session_inputs
            finally:
                await manager.shutdown()
    asyncio.run(scenario())
