"""Relay switch orchestration and passive discovery; synthetic IQ, no RF emission."""
from dataclasses import replace
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from flrc_modem.config import DemodConfig
from flrc_modem.encoding import EncodeContext, load_encoder
from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.io.iq import encode_samples
from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.modulation import ModulationConfig, iter_signal
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.sync.control import run_session
from flrc_modem.sync.live import LiveSyncSession
from flrc_modem.sync.orchestration import validate_plan
from flrc_modem.sync.probe import SyncWordProbe, probe_config, validate_probe
from flrc_modem.sync.transceiver import LiveTDDTransceiver, TDDExchangeResult

ROOT = Path(__file__).resolve().parents[1]
FREQUENCY = 2471660000
BASE, NEW = 'ecee6698', '12345678'
RATE = 2_500_000


def definition():
    value = json.loads((ROOT / 'templates/plans/dk32s_relay_switch_probe.json').read_text(encoding='utf-8'))['plan']
    value['sync_probe']['observe_ms'] = 1
    return value


def raw_message(direction='ground_to_air'):
    return bytes(load_encoder('dk32s')({'direction': direction, 'command': '0x54', 'sequence': 100,
        'fields': {'rc_relay': 1} if direction == 'ground_to_air' else {}}, EncodeContext(0, 0, RATE)))


def frame(when, word=NEW, direction='ground_to_air'):
    return DecodedFrame(when, FREQUENCY, 0, -20, 1, 0,
        PhysicalPacket(bytes.fromhex(word), b'', raw_message(direction), b'', 0))


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, 'passive decoder did not finish'
        time.sleep(.01)


@pytest.fixture
def probe():
    config = DemodConfig(sample_rate=RATE, center_frequency=FREQUENCY, frequencies=(FREQUENCY,))
    value = SyncWordProbe(config, validate_probe({}), plan_id='test',
        excluded_words={BASE, '13119967'}, start_hw_time=100, is_echo=lambda f: f.time_s == 100.5)
    assert value.ready.wait(5)
    yield value
    value.close()


def test_synthetic_unknown_sync_is_recovered_and_crc_verified_without_changing_main_filter(probe):
    packet = encode_packet(raw_message(), bytes.fromhex(NEW), coding_rate='3/4')
    modulation = ModulationConfig(sample_rate=RATE, bit_rate=325000, repeat=3,
                                 guard_seconds=.003, gap_seconds=.005)
    iq = np.concatenate(list(iter_signal(packet.bits, modulation)))
    session = LiveSyncSession(sample_rate=RATE, frequency_hz=FREQUENCY, sync_word=BASE)
    session._on_rate_configured(RATE)
    session.sync_probe = probe
    raw = encode_samples(iq, 'cs8')
    session._metadata_callback(True, 100, 0, len(iq), 0)
    session.window_queue.write(raw)
    session.window_queue.finish()
    count = session.window_queue.ready.qsize()
    session.done_event.set()
    session._dsp_loop()
    wait_for(lambda: probe.snapshot()['processed_windows'] == count)
    result = probe.snapshot()
    assert [(c['sync_word_hex'], c['valid_frames'], c['confirmed']) for c in result['candidates']] == [(NEW, 3, True)]
    assert probe.config.sync_candidates == ()
    assert session.demod_config.sync_candidates == (bytes.fromhex(BASE),)
    assert session.stats.decoded_frames == 0
    assert session.frame_queue.get_nowait() is None
    assert probe.close()['outcome'] == 'candidate_found'


def test_candidates_exclude_original_inverse_echo_air_invalid_ambiguous_and_duplicate_frames(probe):
    for item in (frame(99), frame(100.1, BASE), frame(100.2, '13119967'), frame(100.5),
                 frame(100.3, direction='air_to_ground')):
        probe.accept(item)
    bad = frame(100.6)
    probe.accept(replace(bad, packet=replace(bad.packet, payload=b'invalid')))
    probe.accept(replace(bad, packet=replace(bad.packet, sync_crc_candidates=((bytes.fromhex(NEW), 1), (b'abcd', 2)))))
    probe.accept(frame(101))
    # The same burst seen by overlapping channel filters cannot inflate evidence.
    probe.accept(replace(frame(101.0001), frequency_hz=FREQUENCY + 100000))
    result = probe.snapshot()
    assert len(result['candidates']) == 1 and result['candidates'][0]['valid_frames'] == 1
    assert result['ambiguous_frames'] == result['duplicate_frames'] == 1
    assert probe.close()['outcome'] == 'insufficient_frames'


@pytest.mark.parametrize('options', [None, {'bad': 1}, {'observe_ms': True}, {'observe_ms': 0},
    {'min_frames': 1}, {'min_frames': float('nan')}, {'frequency_offsets_hz': []},
    {'frequency_offsets_hz': [0, 0]}, {'frequency_offsets_hz': [False]}])
def test_invalid_probe_config_rejected(options):
    with pytest.raises(ValueError):
        validate_plan({**definition(), 'sync_probe': options})


def test_frequency_range_and_finite_plan_checked_before_transmit():
    config = DemodConfig(sample_rate=RATE, center_frequency=FREQUENCY, frequencies=(FREQUENCY,))
    assert probe_config(config, validate_probe({'frequency_offsets_hz': [-500000, 0, 500000]})).frequencies == (
        FREQUENCY - 500000, FREQUENCY, FREQUENCY + 500000)
    with pytest.raises(ValueError, match='采样带'):
        probe_config(config, validate_probe({'frequency_offsets_hz': [1000000]}))
    with pytest.raises(ValueError, match='有限 count'):
        validate_plan({**definition(), 'periodic': [{'id': 'infinite', 'interval_ms': 10, 'payload_hex': '00' * 6}]})
    trx = LiveTDDTransceiver.__new__(LiveTDDTransceiver)
    trx.protocol, trx.timed_hopping = 'dk32s', True
    trx.session = SimpleNamespace(demod_config=config)
    with pytest.raises(ValueError, match='关闭定时跳频'):
        trx.validate_sync_probe({})


def test_bounded_probe_queue_drops_old_windows_and_close_never_leaves_queued_iq():
    entered, release = threading.Event(), threading.Event()
    def blocked_decode(*args):
        entered.set()
        assert release.wait(3)
        return []
    config = DemodConfig(sample_rate=RATE, center_frequency=FREQUENCY, frequencies=(FREQUENCY,))
    with patch('flrc_modem.pipeline.WindowDecoder.decode', side_effect=blocked_decode):
        probe = SyncWordProbe(config, validate_probe({}), plan_id='bounded', excluded_words=set(),
                              start_hw_time=0, is_echo=lambda f: False)
        try:
            probe.submit(np.zeros(100, np.complex64), 1)
            assert entered.wait(3)
            for index in range(20):
                probe.submit(np.zeros(100, np.complex64), index + 2)
            assert probe.queue.qsize() == 8
            assert probe.snapshot()['dropped_windows'] == 12
        finally:
            release.set()
            result = probe.close('stopped')
        assert result['worker_stopped'] and probe.queue.empty()


@pytest.mark.parametrize('mode', ['success', 'first_timeout', 'start_failure', 'stop_observing'])
def test_real_session_runs_template_order_observes_and_cleans_up_on_failure_or_stop(mode):
    trx, output, sent = MagicMock(), io.StringIO(), []
    trx.session.sample_rate = RATE
    trx.sync_diagnostics.return_value = {'state': 'locked'}
    plan = definition()
    second_sent = threading.Event()
    snapshot = {'plan_id': plan['plan_id'], 'status': 'running', 'candidates': [], 'error': None}
    trx.start_sync_probe.return_value = snapshot
    trx.sync_probe_snapshot.return_value = snapshot
    trx.finish_sync_probe.return_value = {**snapshot, 'status': 'finished', 'outcome': 'not_observed'}
    if mode == 'start_failure':
        trx.start_sync_probe.side_effect = ValueError('not locked')
    if mode == 'stop_observing':
        plan['sync_probe']['observe_ms'] = 60000
    def send(payload, wait_reply, **kwargs):
        trx.start_sync_probe.assert_called_once()
        sent.append((payload['command'], wait_reply))
        if len(sent) == 2:
            second_sent.set()
        return TDDExchangeResult(True, b'123456', 101, 0, FREQUENCY,
            tx_completed=True, reply_matched=wait_reply and mode != 'first_timeout')
    trx.send_and_wait_reply.side_effect = send
    class Input:
        count = 0
        def readline(self, limit):
            self.count += 1
            if self.count == 1:
                return json.dumps({'action': 'start_plan', 'plan': plan}) + '\n'
            if self.count == 2 and mode == 'stop_observing':
                assert second_sent.wait(3)
                return json.dumps({'action': 'stop_plan', 'plan_id': plan['plan_id']}) + '\n'
            return ''
    result = run_session(trx, SimpleNamespace(timeout=1, sync_timeout=5, plan_file='test.json'),
                         UIEventEmitter(output, True), Input())
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    states = [item['payload']['status'] for item in records if item['type'] == 'duplex.plan']
    if mode == 'start_failure':
        assert not sent and result == 1
        trx.finish_sync_probe.assert_not_called()
    else:
        assert sent == ([('0x54', True)] if mode == 'first_timeout' else [('0x54', True), ('0x55', False)])
        trx.finish_sync_probe.assert_called_once()
        assert any(item['type'] == 'duplex.probe' for item in records)
    assert states[-1] == {'success': 'completed', 'first_timeout': 'failed',
                           'start_failure': 'failed', 'stop_observing': 'stopped'}[mode]
    if mode == 'success':
        assert 'observing' in states and result == 0


def test_live_session_forks_owned_iq_and_keeps_learned_main_sync_filter_isolated():
    session = LiveSyncSession(sample_rate=RATE, sync_word=None)
    session._on_rate_configured(RATE)
    session.sync_probe = MagicMock(excluded={BASE, '13119967'})
    session._metadata_callback(True, 100, 0, session.window_queue.capacity // 2, 0)
    session.window_queue.write(b'\0' * session.window_queue.capacity)
    session.done_event.set()
    from flrc_modem.pipeline import WindowDecoder
    with patch('flrc_modem.sync.live.WindowDecoder', wraps=WindowDecoder) as factory, \
         patch('flrc_modem.sync.live.WindowDecoder.decode', return_value=[]) as decode:
        session._dsp_loop()
    submitted, when = session.sync_probe.submit.call_args.args
    assert submitted.dtype == np.complex64 and when == 100
    assert set(factory.call_args.args[0].sync_candidates) == {bytes.fromhex(BASE), bytes.fromhex('13119967')}
    assert session.state_machine.sync_word is None


def test_cli_plan_file_loads_wrapper_and_returns_failure_without_hardware(tmp_path, capsys):
    from flrc_modem.duplex_cli import main
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'plan': definition()}), encoding='utf-8')
    trx = MagicMock()
    trx.__enter__.return_value = trx
    trx.session.stats.error = None
    trx.session.sample_rate = RATE
    trx.device.device_info = {}
    trx.sync_diagnostics.return_value = {'state': 'locked'}
    trx.start_sync_probe.return_value = {'plan_id': 'test', 'status': 'running'}
    trx.finish_sync_probe.return_value = {'plan_id': 'test', 'status': 'failed', 'error': None}
    trx.send_and_wait_reply.return_value = TDDExchangeResult(False, b'123456', 100, 0, FREQUENCY,
        tx_completed=True, reply_matched=False)
    with patch('flrc_modem.duplex_cli.LiveTDDTransceiver', return_value=trx):
        assert main(['--plan-file', str(path), '--sync', BASE]) == 1
    trx.send_and_wait_reply.assert_called_once()
    assert 'reply_timeout' in capsys.readouterr().out


def test_web_preflight_reuses_cli_plan_loading_and_validates_probe_without_radio():
    from webui.backend.app.commands.preflight import preflight
    from webui.backend.app.commands.registry import CommandRegistry
    parameters = {'plan_file': str(ROOT / 'templates/plans/dk32s_relay_switch_probe.json'),
                  'config': str(ROOT / 'templates/physical/dk32s.toml'), 'payload_hex': 'invalid', 'sync': BASE}
    argv = CommandRegistry().build_cli_argv('duplex', parameters)
    assert '--plan-file' in argv and '--payload-hex' not in argv
    result = preflight('duplex', argv)
    assert result['plan'] == validate_plan({**definition(), 'sync_probe': validate_probe({})})
    assert result['payload_bytes'] is None
    with pytest.raises(ValueError, match='关闭定时跳频'):
        preflight('duplex', CommandRegistry().build_cli_argv('duplex', {**parameters, 'timed_hopping': True}))


def test_persisted_probe_survives_status_events_and_marks_crashed_observation_interrupted(tmp_path):
    from webui.backend.app.storage.db import Database
    database = Database(tmp_path / 'probe.db')
    database.insert_run({'id': 'probe', 'command_id': 'duplex', 'command_args': {'session': True},
                         'cli_argv': [], 'status': 'RUNNING', 'created_at': 'now'})
    snapshot = {'plan_id': 'probe-plan', 'status': 'running', 'candidates': [{'sync_word_hex': NEW}]}
    database.insert_event('probe', 1, 'duplex.probe', None, 'now', snapshot)
    database.insert_event('probe', 2, 'duplex.plan', None, 'now', {'plan_id': 'probe-plan', 'status': 'observing'})
    for sequence in range(3, 60):
        database.insert_event('probe', sequence, 'duplex.status', None, 'now', {'state': 'locked'})
    assert database.get_message_state('probe')['probe'] == snapshot
    database.update_run_status('probe', 'FAILED')
    result = database.get_message_state('probe')
    assert result['plan']['status'] == result['probe']['status'] == 'interrupted'
    assert result['probe']['candidates'] == snapshot['candidates']
