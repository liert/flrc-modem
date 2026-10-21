"""Profile, persistent control and device ownership regression checks; no physical radio."""
import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.modulation import ModulationConfig
from flrc_modem.modulate_cli import main as modulate_main
from flrc_modem.physical_cli import resolve_profile
from flrc_modem.phy.packet import decode_packet
from flrc_modem.phy.sync import PREFIX_BITS
from flrc_modem.sync import TDDExchangeResult
from flrc_modem.sync.control import run_session, validate_request
from flrc_modem.transmission.source import PacketSource
from flrc_modem.transmit_cli import make_argument_parser, main as transmit_main
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.devices.manager import DeviceManager
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.schemas import DeviceInfo, DeviceStatus
from webui.backend.app.storage.db import Database

ROOT = Path(__file__).resolve().parents[1]
H12 = ROOT / 'templates/physical/h12.toml'
SYNC = '54313253'


def test_h12_receive_candidates_cannot_silently_choose_transmit_seed(monkeypatch):
    sender = MagicMock()
    monkeypatch.setattr('flrc_modem.transmit_cli.LiveTransmitter', sender)
    assert transmit_main(['--config', str(H12), '--payload-hex', '00' * 32,
                          '--sync', SYNC, '--frequency', '2400000000']) == 2
    sender.assert_not_called()
    args = make_argument_parser().parse_args(['--config', str(H12), '--payload-hex', '00' * 32,
        '--crc-initial', '0', '--sync', SYNC, '--frequency', '2400000000'])
    profile = resolve_profile(args)
    assert (profile.packet.header_type, profile.packet.crc_bytes, profile.packet.crc_initial) == ('fixed', 4, 0)
    assert (profile.sample_rate, profile.bit_rate, profile.coding_rate, profile.bt) == (8000000, 1300000, '1', 0)
    args.frequency = None
    with pytest.raises(ValueError, match='实际 RF'):
        resolve_profile(args)


def test_transmit_section_overrides_receive_and_cli_overrides_transmit(tmp_path):
    path = tmp_path / 'profile.toml'
    path.write_text('[demod]\ncrc_bytes=4\ncrc_initials=[0,4294967295]\nheader_type="fixed"\n'
                    '[transmit]\ncrc_initial=0\nfrequency=2400000000\nsync_word="54313253"\n'
                    'payload_lengths=[32]\nbit_rate=1300000\ncoding_rate="1"\nbt=0\n', encoding='utf-8')
    args = make_argument_parser().parse_args(['--config', str(path), '--payload-hex', '00' * 32])
    assert resolve_profile(args).packet.crc_initial == 0
    args.crc_initial = 0xFFFFFFFF
    assert resolve_profile(args).packet.crc_initial == 0xFFFFFFFF


@pytest.mark.parametrize('dynamic', [False, True])
def test_live_source_uses_fixed_crc32_for_static_and_dynamic_payloads(monkeypatch, dynamic):
    observed = []
    def waveform(bits, config):
        observed.append(bits.copy())
        return np.ones(128, dtype=np.complex64)
    monkeypatch.setattr('flrc_modem.transmission.source.modulate_bits', waveform)
    source = PacketSource(ModulationConfig(sample_rate=8000000, bit_rate=1300000, coding_rate='1', bt=0,
                                          guard_seconds=0, gap_seconds=0), bytes.fromhex(SYNC), 0,
                          header_type='fixed', crc_bytes=4, payload_lengths=(32,))
    if dynamic:
        source.submit_message({}, lambda message, context: bytes([context.frame_index]) * 32, repeat=2)
    else:
        source.submit(bytes(32))
    with pytest.raises(ValueError, match='固定包长'):
        source.submit(bytes(42))
    source.finish()
    destination = bytearray(4096)
    while source.readinto(destination):
        pass
    assert len(observed) == (2 if dynamic else 1)
    for index, bits in enumerate(observed):
        packet = decode_packet(bits[PREFIX_BITS:], (bytes.fromhex(SYNC),), 0, '1',
                               header_type='fixed', crc_bytes=4, payload_lengths=(32,))
        assert packet.header == b'' and len(packet.crc) == 4
        assert packet.payload == bytes([index]) * 32


def test_h12_fixed_crc32_iq_generation_verifies_using_receive_config(tmp_path):
    # Entire waveform/receiver path; no driver is loaded.
    capture = next(item for item in json.loads((ROOT / 'tests/fixtures/h12_packets.json').read_text())
                   if item['kind'] == 'pairing_ack')
    output = tmp_path / 'pairing.cs8'
    assert modulate_main(['--config', str(H12), '--crc-initial', '0', '--sync', SYNC,
        '--payload-hex', capture['payload_hex'], '--output', str(output), '--verify']) == 0
    metadata = json.loads(Path(str(output) + '.json').read_text())
    assert metadata['packet_format']['crc_bytes'] == 4
    assert metadata['crc_hex'] == capture['crc_hex']


def test_preflight_reports_effective_config_without_driver_and_rejects_unsupported_timing():
    registry = CommandRegistry()
    values = dict(config=str(H12), payload_hex='00' * 32, crc_initial='0', sync=SYNC, frequency=2400000000,
                  message_file='does-not-exist.json', protocol='dk32s', serial='radio-a')
    argv = registry.build_cli_argv('transmit', values)
    assert '--message-file' not in argv and '--protocol' not in argv
    info = preflight('transmit', argv)
    assert info['sample_rate'] == 8000000 and info['payload_bytes'] == 32
    assert info['device']['selection'] == 'radio-a'
    json.dumps(info, allow_nan=False)
    values = dict(config=str(H12), crc_initial='0', sync=SYNC, session=True)
    with pytest.raises(ValueError, match='时隙模型'):
        preflight('duplex', registry.build_cli_argv('duplex', values))
    argv = registry.build_cli_argv('duplex', {'session': True, 'message_file': 'unused.json', 'payload_hex': 'bad'})
    assert '--message-file' not in argv and '--payload-hex' not in argv
    assert preflight('duplex', argv)['session'] is True


def test_session_only_sends_submitted_requests_and_survives_reply_timeout():
    trx = MagicMock()
    trx.sync_diagnostics.return_value = {'state': 'lost', 'error': None}
    trx.send_and_wait_reply.side_effect = [
        TDDExchangeResult(False, b'123456', 0, 0, 2400000000, error='失锁'),
        TDDExchangeResult(False, b'123456', 1, 2, 2400000000, tx_completed=True, error='回复超时'),
        TDDExchangeResult(True, b'123456', 2, 3, 2400000000, tx_completed=True, reply_matched=True)]
    requests = [dict(request_id=str(index), payload_hex='313233343536') for index in range(3)]
    requests += [requests[0], {'request_id': 'invalid', 'payload_hex': '00'}]
    stream, output = io.StringIO(''.join(json.dumps(item) + '\n' for item in requests)), io.StringIO()
    assert run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(output, True), stream) == 0
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    results = [event['payload'] for event in events if event['type'] == 'duplex.result']
    by_id = {result['request_id']: result for result in results if result['status'] != 'rejected'}
    assert [by_id[str(index)]['status'] for index in range(3)] == ['send_failed', 'reply_timeout', 'reply_matched']
    assert trx.send_and_wait_reply.call_count == 3
    trx.send_and_wait_reply.reset_mock()
    assert run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(io.StringIO(), True), io.StringIO()) == 0
    trx.send_and_wait_reply.assert_not_called()


@pytest.mark.parametrize('payload', [[], {}, {'request_id': 'a', 'payload_hex': '123'},
    {'request_id': 'a', 'payload_hex': '00'*6, 'message': {}},
    {'request_id': 'a', 'message': []}, {'request_id': 'a', 'message': {}, 'wait_reply': 'false'}])
def test_control_rejects_malformed_requests(payload):
    with pytest.raises((ValueError, TypeError)):
        validate_request(payload)


def test_device_identity_locks_allow_two_radios_but_reject_alias_and_automatic_conflicts():
    manager = DeviceManager()
    manager._device_cache['hackrf'] = (0, DeviceInfo(device_type='hackrf', id='a', name='HackRF',
        details={'serials': ['0000ABCD', '0000EF01']}, status=DeviceStatus.ONLINE))
    first = manager.resolve_selector('hackrf', {'serial': 'abcd'})
    assert first == manager.resolve_selector('hackrf', {'serial': '0000ABCD'})
    second = manager.resolve_selector('hackrf', {'serial': 'ef01'})
    assert manager.acquire_device('hackrf', 'first', first)[0]
    assert manager.acquire_device('hackrf', 'second', second)[0]
    assert not manager.acquire_device('hackrf', 'alias', first)[0]
    assert not manager.acquire_device('hackrf', 'auto')[0]
    manager.release_device('first'); manager.release_device('second')
    assert manager.acquire_device('hackrf', 'auto')[0]
    assert not manager.acquire_device('hackrf', 'explicit', first)[0]
    manager._device_cache['usrp'] = (0, DeviceInfo(device_type='usrp', id='a', name='USRP',
        details={'devices': [{'addr': '192.168.10.2', 'serial': 'ABC'}]}, status=DeviceStatus.ONLINE))
    assert manager.resolve_selector('usrp', {'args': 'addr=192.168.10.2'}) == 'abc'
    assert manager.resolve_selector('usrp', {'args': 'serial=ABC,master_clock_rate=125000000'}) == 'abc'
    assert manager.resolve_selector('usrp', {'args': 'addr=192.168.10.2,second_addr=other'}) is None


def test_session_history_keeps_results_when_status_events_dominate(tmp_path):
    database = Database(tmp_path / 'history.db')
    database.insert_event('session', 1, 'duplex.result', None, 'now', {'request_id': 'one', 'status': 'reply_timeout'})
    for seq in range(2, 2050):
        database.insert_event('session', seq, 'duplex.status', None, 'now', {'state': 'locked'})
    state = database.get_message_state('session')
    assert state['results'][0]['request_id'] == 'one'
    assert state['status']['state'] == 'locked'


def test_job_input_pipe_is_bounded_correlated_and_closed_sessions_reject():
    async def scenario():
        manager = JobManager()
        stdin = MagicMock()
        async def drain(): pass
        stdin.drain = drain
        proc = SimpleNamespace(returncode=None, stdin=stdin)
        manager._active_processes['session'] = proc
        state = {'ready': True, 'lock': asyncio.Lock(), 'pending': set(), 'seen': set(), 'sample_rate': 2500000}
        manager._session_inputs['session'] = state
        request = {'request_id': 'first', 'payload_hex': '00' * 6, 'wait_reply': False}
        assert (await manager.send_message('session', request))['status'] == 'accepted'
        assert json.loads(stdin.write.call_args.args[0]) == request
        with pytest.raises(RuntimeError, match='已提交'):
            await manager.send_message('session', request)
        state['pending'] = {str(index) for index in range(16)}
        with pytest.raises(RuntimeError, match='队列已满'):
            await manager.send_message('session', {**request, 'request_id': 'next'})
        manager._stop_requests.add('session')
        with pytest.raises(RuntimeError, match='停止'):
            await manager.send_message('session', {**request, 'request_id': 'next'})
    asyncio.run(scenario())


def test_web_session_api_and_subprocess_pipe_end_to_end_without_radio(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from webui.backend.app.main import app
    from webui.backend.app.events.hub import EventHub
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.devices import manager as device_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.jobs import manager as job_module
    database, devices, jobs = Database(tmp_path / 'web.db'), DeviceManager(), JobManager()
    monkeypatch.setattr(db_module, '_DB', database)
    monkeypatch.setattr(device_module, '_DEVICE_MGR', devices)
    monkeypatch.setattr(hub_module, '_EVENT_HUB', EventHub())
    monkeypatch.setattr(job_module, '_JOB_MGR', jobs)
    original_spawn = asyncio.create_subprocess_exec
    script = '''
import json,sys
def emit(kind,payload):
 print(json.dumps(dict(type=kind,seq=1,payload=payload)),flush=True)
emit('duplex.ready',dict(sample_rate=2500000,device_args='fake',channel=0))
for line in sys.stdin:
 record=json.loads(line)
 if record.get('action')=='stop': break
 emit('duplex.result',dict(request_id=record['request_id'],status='tx_completed',tx_completed=True,reply_matched=False))
'''
    async def fake_spawn(*argv, **kwargs):
        assert '--session' in argv
        assert kwargs['stdin'] == asyncio.subprocess.PIPE
        return await original_spawn(sys.executable, '-u', '-c', script, **kwargs)
    monkeypatch.setattr(job_module.asyncio, 'create_subprocess_exec', fake_spawn)

    async def wait_until(predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 3)

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            check = await client.post('/api/v1/commands/duplex/preflight', json={
                'command_id': 'duplex', 'parameters': {'session': True}})
            assert check.status_code == 200 and check.json()['timing_model'] == 'DK32S FHSS/TDD'
            response = await client.post('/api/v1/runs', json={'command_id': 'duplex', 'parameters': {'session': True}})
            assert response.status_code == 200, response.text
            ident = response.json()['id']
            try:
                await wait_until(lambda: jobs._session_inputs.get(ident, {}).get('ready'))
                for number in range(2):
                    payload = {'request_id': str(number), 'payload_hex': '00' * 6, 'wait_reply': False}
                    sent = await client.post(f'/api/v1/runs/{ident}/messages', json=payload)
                    assert sent.status_code == 200, sent.text
                await wait_until(lambda: len(database.get_message_state(ident)['results']) == 2)
                snapshot = (await client.get(f'/api/v1/runs/{ident}/session')).json()
                assert snapshot['ready']['device_args'] == 'fake'
                assert [item['request_id'] for item in snapshot['results']] == ['1', '0']
                assert (await client.post(f'/api/v1/runs/{ident}/messages', json=payload)).status_code == 409
                assert (await client.post(f'/api/v1/runs/{ident}/messages', json={'message': []})).status_code == 400
                stopped = await client.post(f'/api/v1/runs/{ident}/stop')
                assert stopped.status_code == 200
                assert database.get_run(ident)['status'] == 'CANCELLED'
                assert devices.get_lock_info('usrp') is None
                assert ident not in jobs._session_inputs
                assert (await client.post(f'/api/v1/runs/{ident}/messages', json=payload)).status_code == 409
            finally:
                await jobs.shutdown()
    asyncio.run(scenario())


def test_stop_control_drops_queued_requests_before_any_transmission():
    trx = MagicMock()
    trx.sync_diagnostics.return_value = {'state': 'locked', 'error': None}
    stream = io.StringIO('{"action":"stop"}\n{"request_id":"late","payload_hex":"000000000000"}\n')
    assert run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5), UIEventEmitter(io.StringIO(), True), stream) == 0
    trx.request_stop.assert_called_once()
    trx.send_and_wait_reply.assert_not_called()


def test_startup_failure_closes_owned_device_without_starting_receiver(monkeypatch):
    from flrc_modem.sync import LiveTDDTransceiver
    device = MagicMock()
    device.handle.value = False
    device.prepare_transmit.side_effect = OSError('TX 初始化失败')
    monkeypatch.setattr('flrc_modem.sync.transceiver.USRPDevice', lambda **kwargs: device)
    trx = LiveTDDTransceiver()
    with pytest.raises(OSError, match='初始化失败'):
        trx.start()
    device.close.assert_called_once()
    assert trx.session._rx_thread is None


def test_sync_hackrf_selection_reaches_device_constructor(monkeypatch):
    from flrc_modem.sync import LiveSyncSession
    device, api = MagicMock(), object()
    receiver = device.return_value.__enter__.return_value
    receiver.selected_device = {'serial': '0000ABCD', 'index': 1}
    monkeypatch.setattr('flrc_modem.sdr.hackrf.HackRFLibrary', lambda: api)
    monkeypatch.setattr('flrc_modem.sdr.hackrf.HackRFDevice', device)
    session = LiveSyncSession(sdr_type='hackrf', serial='ABCD')
    session._rx_loop()
    device.assert_called_once_with(api, 'ABCD', device_index=None)
    receiver.receive.assert_called_once()
    assert not session.stats.error
