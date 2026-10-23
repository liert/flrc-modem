"""Standalone parsing uses the CLI payload path without manufacturing RF evidence."""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.parsing.api import load_parser, load_payload_parser
from flrc_modem.parsing.inspection import inspect_payload
from webui.backend.app.main import app
from test_protocols import frame_from_payload, sample, packed_channels


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('protocol', ['dk32s', 'dk32s-multi', 'datalink'])
def test_inspection_matches_cli_and_reports_actual_encapsulation(protocol):
    if protocol == 'datalink':
        fixture = json.loads((Path(__file__).parent / 'fixtures/datalink_packets.json').read_text())[0]
        frame = frame_from_payload(bytes.fromhex(fixture['payload_hex']))
    elif protocol == 'dk32s-multi':
        frame = sample(0x11, b'\x05\x02' + packed_channels(list(range(16))), timestamp=123)
    else:
        fixture = json.loads((Path(__file__).parent / 'fixtures/dk32s_packet.json').read_text())
        frame = frame_from_payload(bytes.fromhex(fixture['payload_hex']))
    payload = frame.packet.payload
    result = inspect_payload(payload.hex(' ').upper(), protocol)
    assert result['status'] == 'parsed'
    assert result['application'] == load_parser(protocol)(frame) == load_payload_parser(protocol)(payload)
    assert result['payload_hex'] == payload.hex()
    assert result['physical_crc_status'] == 'not_checked'
    assert not {'crc_ok', 'sync_word_hex', 'time_s', 'frequency_hz'} & result.keys()
    command = next(span for span in result['ranges'] if span['path'] == 'command')
    assert command['offset'] == (4 if protocol == 'datalink' else 8)
    assert int(command['raw_hex'], 16) == result['application']['command']
    sequence = next(span for span in result['ranges'] if span['path'] == 'sequence')
    assert int.from_bytes(bytes.fromhex(sequence['raw_hex']), 'little') >> 1 == result['application']['sequence']
    for span in result['ranges']:
        assert 0 <= span['offset'] < len(payload)
        assert 0 < span['length'] <= len(payload) - span['offset']
        assert bytes.fromhex(span['raw_hex']) == payload[span['offset']:span['offset'] + span['length']]


@pytest.mark.parametrize('command', [0x54, 0x55])
def test_relay_switch_and_ack_share_existing_parser(command):
    for timestamp, data in ((77, b'\x01'), (None, b'')):
        frame = sample(command, data, timestamp=timestamp, need_ack=timestamp is not None, is_ack=timestamp is None)
        result = inspect_payload(frame.packet.payload.hex(), 'dk32s')
        assert result['status'] == 'parsed'
        assert result['application'] == load_parser('dk32s')(frame)
        assert result['application']['is_ack'] is (timestamp is None)
        assert next(span for span in result['ranges'] if span['path'] == 'command')['offset'] == (8 if timestamp else 4)


def test_unknown_commands_keep_bytes_and_invalid_checksum_does_not_claim_success():
    payload = sample(0xEE, b'\x12\x34', timestamp=1).packet.payload
    partial = inspect_payload(payload.hex(), 'dk32s')
    assert partial['status'] == 'partial'
    assert partial['application']['fields'] == {'kind': 'unknown', 'raw_hex': '1234'}
    damaged = payload[:-1] + bytes((payload[-1] ^ 1,))
    failed = inspect_payload(damaged.hex(), 'dk32s')
    assert failed['status'] == 'error' and 'sum8' in failed['error']
    assert failed['payload_hex'] == damaged.hex() and failed['application'] is None and failed['ranges'] == []


@pytest.mark.anyio
async def test_decode_api_catalog_validation_and_no_device_database_access(monkeypatch):
    from webui.backend.app.api import router
    def forbidden():
        raise AssertionError('Payload parsing must not touch devices or database')
    monkeypatch.setattr(router, 'get_device_manager', forbidden)
    monkeypatch.setattr(router, 'get_db', forbidden)
    payload = sample(0x54, b'\x01', timestamp=0, need_ack=True).packet.payload.hex()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        catalog = await client.get('/api/v1/protocols')
        assert catalog.status_code == 200
        assert {entry['id'] for entry in catalog.json()} == {'dk32s', 'dk32s-multi', 'datalink'}
        assert all('parser' not in entry and 'payload_parser' not in entry for entry in catalog.json())
        response = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': payload})
        assert response.status_code == 200 and response.json()['status'] == 'parsed'
        for text in ('', '123', 'gg', '12:34', '0x1234', '00' * 1025):
            invalid = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': text})
            assert invalid.status_code in (400, 422), text
        for protocol in ('os:system', 'auto', 'missing'):
            unsupported = await client.post('/api/v1/protocols/decode', json={'protocol': protocol, 'payload_hex': payload})
            assert unsupported.status_code == 400
        oversized = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': '0' * 8193})
        assert oversized.status_code == 422
        extra = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': payload, 'parser': 'os:system'})
        assert extra.status_code == 422
        malformed = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': '00'})
        assert malformed.status_code == 200 and malformed.json()['status'] == 'error'
