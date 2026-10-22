"""The PHY workbench and TDD preview must use the same physical packet."""
import json
import pytest
from httpx import ASGITransport, AsyncClient

from webui.backend.app.main import app
from webui.backend.app.commands.registry import get_command_registry
from webui.backend.app.commands.preflight import preflight
from flrc_modem.duplex_cli import make_argument_parser, resolve_configuration


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@pytest.mark.parametrize('header', ['fixed', 'variable'])
async def test_linked_timing_uses_actual_encoded_frame_and_separate_reply_size(header):
    frame = {'payload_hex': '11' * 24, 'sync_word': 'ecee6698', 'header_type': header,
             'coding_rate': '1/2', 'sample_rate': 4000000, 'bit_rate': 650000,
             'amplitude': 0.4, 'ramp_time_us': 20}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        phy = await client.post('/api/v1/modulate/preview', json=frame)
        preview = await client.post('/api/v1/sync/preview-schedule', json={'frame': frame, 'payload_bytes': 7})
        assert preview.status_code == 200
        linked = preview.json()['frame_summary']
        assert linked['tx_payload_bytes'] == 24
        assert linked['reply_payload_bytes'] == 7
        assert linked['total_bits'] == phy.json()['total_bits']
        assert linked['tx_duration_us'] == phy.json()['duration_us']
        assert linked['header_type'] == header
        timing = preview.json()['timing_breakdown']
        assert abs(timing['ground_tx_ms'] - linked['tx_duration_us'] / 1000) < .001
        assert abs(sum(timing[key] for key in ('ground_tx_ms', 'turnaround_guard_ms', 'air_rx_ms', 'slot_guard_ms')) - 50) < .003


@pytest.mark.anyio
async def test_linked_frame_overflow_rejected_even_when_independent_defaults_fit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/sync/preview-schedule', json={
            'dwell_ms': 3, 'payload_bytes': 6, 'bit_rate': 1300000,
            'frame': {'payload_hex': '11' * 127, 'bit_rate': 260000, 'coding_rate': '1/2'},
        })
        assert response.status_code == 400
        assert '驻留时间不足' in response.json()['detail']


def test_linked_session_registry_parser_and_preflight_agree():
    args = {'session': True, 'sdr': 'usrp', 'args': 'addr=192.168.10.2', 'frequency': 2471660000,
            'sample_rate': 2500000, 'gain': 30, 'tx_gain': 25, 'protocol': 'dk32s',
            'sync': 'ecee6698', 'bit_rate': 325000, 'cr': '1/2', 'header_type': 'variable',
            'amplitude': 0.4, 'ramp_time_us': 20}
    argv = get_command_registry().build_cli_argv('duplex', args)
    parsed = make_argument_parser().parse_args(argv[4:])
    profile, _ = resolve_configuration(parsed)
    assert parsed.payload_hex is None  # Start must not transmit the draft automatically.
    assert parsed.amplitude == .4
    assert parsed.ramp_time_us == 20
    assert profile.packet.header_type == 'variable'
    assert profile.packet.payload_lengths == ()
    result = preflight('duplex', argv)
    assert result['session'] is True
    assert result['packet']['header_type'] == 'variable'


@pytest.mark.anyio
async def test_preview_and_live_session_resolve_the_same_custom_hopping_table(tmp_path):
    table = [2411000000, 2444000000, 2477000000]
    path = tmp_path / 'current radio.json'
    path.write_text(json.dumps({'hopping_table': table}), encoding='utf-8')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/sync/preview-schedule', json={'rf_config': str(path)})
    assert response.status_code == 200
    assert response.json()['frequencies'] == table
    argv = get_command_registry().build_cli_argv('duplex', {'session': True, 'rf_config': str(path)})
    result = preflight('duplex', argv)
    assert result['rf_config']['hopping_table'] == table


@pytest.mark.anyio
async def test_invalid_hopping_config_does_not_silently_fall_back(tmp_path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/sync/preview-schedule', json={'rf_config': str(tmp_path / 'missing.json')})
    assert response.status_code == 400
