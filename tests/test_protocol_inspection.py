"""Standalone parsing uses the CLI payload path without manufacturing RF evidence."""
import json
import struct
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.parsing.api import load_parser, load_payload_parser
from flrc_modem.parsing.inspection import inspect_payload
from webui.backend.app.main import app
from test_protocols import frame_from_payload, sample, packed_channels


def test_business_ranges_cover_decoded_fields_without_inventing_bytes():
    channels = packed_channels(list(range(16)))
    # Distinct layouts, including both envelope offsets, empty bodies and opaque data.
    cases = [
        ('dk32s', 0x13, bytes(range(24)), {'timestamp': 0}),
        ('dk32s', 0x13, b'\x11\x22\x33\x44', {'is_ack': True}),
        ('dk32s', 0x14, b'', {'timestamp': 1}),
        ('dk32s', 0x11, channels + b'\x07ABC', {'timestamp': 1}),
        ('dk32s-multi', 0x16, b'\x05\x02' + channels + b'\x07', {'timestamp': 1}),
        ('dk32s', 0x5E, b'\x01\x02\x03\x04' + channels, {'timestamp': 1}),
        ('dk32s', 0x5F, b'\x01' + channels, {'timestamp': 1}),
        ('dk32s', 0x5F, b'\x01', {}),
        ('dk32s', 0x16, b'\x07ABC', {}),
        ('dk32s', 0x46, b'\x07ABC\x34\x12', {}),
        ('dk32s', 0x17, (-53).to_bytes(4, 'little', signed=True), {'timestamp': 1}),
        ('dk32s', 0x18, b'\x01\x02', {'timestamp': 1}),
        ('dk32s', 0x12, b'\x02\x05\x80' + channels, {'timestamp': 1}),
        ('dk32s', 0x12, b'\x01', {}),
        ('dk32s', 0x19, (12000).to_bytes(4, 'little'), {'timestamp': 1}),
        ('dk32s', 0x21, bytes(range(12)), {}),
        ('dk32s', 0x22, b'\x01\x02\x03\x04', {}),
        ('dk32s', 0x23, bytes(range(20)), {}),
        ('dk32s', 0x40, b'\x05', {'timestamp': 1}),
        ('dk32s', 0x40, b'\xff', {'timestamp': 1}),
        ('dk32s', 0x26, b'', {'timestamp': 1}),
        ('dk32s', 0x26, b'\x01', {}),
        ('dk32s', 0x30, b'\x01\x02\x03', {'timestamp': 1}),
        ('dk32s', 0x50, b'\x01', {}),
        ('dk32s', 0x5C, bytes(range(41)), {'timestamp': 1}),
        ('dk32s', 0x60, bytes(range(34)), {'timestamp': 1}),
        ('dk32s', 0x60, bytes(range(10)), {}),
        ('dk32s', 0x62, bytes(range(12)), {'timestamp': 1}),
        ('dk32s', 0x64, b'\x01\x06\x32', {}),
        ('dk32s', 0xF1, b'\x01', {}),
        ('dk32s', 0x55, b'\x01', {'timestamp': 1}),
        ('dk32s', 0x55, b'', {'timestamp': 1}),
        ('dk32s', 0x40, b'', {'is_ack': True}),
        ('dk32s', 0xEE, b'\xaa\xbb', {}),
        ('datalink', 0x13, bytes(range(24)), {'need_ack': True}),
        ('datalink', 0xF2, struct.pack('<f', .125) + b'\x07ABC', {'need_ack': True}),
        ('datalink', 0xF2, b'\x07ABC', {'is_ack': True}),
        ('datalink', 0xF7, b'\x05', {'is_ack': True}),
        ('datalink', 0xF3, b'\x01', {'is_ack': True}),
        ('datalink', 0x17, struct.pack('<f', .5), {'need_ack': True}),
        ('datalink', 0x18, b'\x01\x02', {'is_ack': True}),
        ('datalink', 0xF0, b'\x01\x02\x03\x04', {}),
        ('datalink', 0xEE, b'', {}),
    ]
    for protocol, command, data, options in cases:
        payload = sample(command, data, **options).packet.payload
        result = inspect_payload(payload.hex(), protocol)
        assert result['application'] == load_payload_parser(protocol)(payload)
        assert result['status'] in ('parsed', 'partial'), (protocol, command, result)
        spans = {span['path']: span for span in result['ranges'] if span.get('section') == 'business'}
        fields = result['application']['fields']
        expected = set()
        for key, value in fields.items():
            if key == 'rc_mode' or value == '' or value == []:
                continue  # Mode is an explicit parser choice; empty values have no bytes.
            expected.add('fields.' + key)
            if isinstance(value, list):
                expected.update(f'fields.{key}.{i}' for i in range(len(value)))
        assert set(spans) == expected, (protocol, command, set(spans) ^ expected)
        for path, span in spans.items():
            assert 0 <= span['offset'] < len(payload) - 1, (protocol, command, path)
            assert 0 < span['length'] <= len(payload) - 1 - span['offset']
            assert span['raw_hex'] == payload[span['offset']:span['offset'] + span['length']].hex()
            if 'bit_length' in span:
                assert 0 <= span['bit_offset'] < 8
                assert span['bit_offset'] + span['bit_length'] <= span['length'] * 8


@pytest.mark.parametrize('protocol,command,prefix,base', [
    ('dk32s', 0x11, b'', 9),
    ('dk32s-multi', 0x16, b'\x05\x02', 11),
    ('dk32s', 0x12, b'\x02\x05\x80', 12),
    ('dk32s', 0x5E, b'\x01\x02\x03\x04', 13),
])
def test_business_channel_ranges_extract_exact_little_endian_bits(protocol, command, prefix, base):
    values = [0, 2047, 1, 1024, 3, 2046, 17, 511] * 2
    result = inspect_payload(sample(command, prefix + packed_channels(values), timestamp=1).packet.payload.hex(), protocol)
    spans = {span['path']: span for span in result['ranges']}
    for index, value in enumerate(values):
        raw = spans[f'fields.channels_raw.{index}']
        output = spans[f'fields.channels_output.{index}']
        assert raw['offset'] == base + index * 11 // 8
        assert raw['bit_offset'] == index * 11 % 8
        assert raw['bit_length'] == 11
        assert (int.from_bytes(bytes.fromhex(raw['raw_hex']), 'little') >> raw['bit_offset']) & 2047 == value
        assert output['offset'] == raw['offset'] and output['raw_hex'] == raw['raw_hex']
        assert output['derived'] is True and output['value'] == value + 500
    if command == 0x12:
        for index in range(16):
            span = spans[f'fields.channel_enabled.{index}']
            assert bool((int(span['raw_hex'], 16) >> span['bit_offset']) & 1) == span['value']


def test_business_pwm_pairing_and_float_ranges_have_precise_sources():
    packed = sum(value << (5 * index) for index, value in enumerate(range(1, 10)))
    result = inspect_payload(sample(0x21, packed.to_bytes(6, 'little') + b'\xaa' * 6).packet.payload.hex(), 'dk32s')
    spans = {span['path']: span for span in result['ranges']}
    for index in range(9):
        span = spans[f'fields.channel_mapping.{index}']
        assert (int.from_bytes(bytes.fromhex(span['raw_hex']), 'little') >> span['bit_offset']) & 31 == index + 1
    assert spans['fields.unused_tail_hex']['offset'] == 11
    assert spans['fields.unused_tail_hex']['raw_hex'] == 'aa' * 6
    payload = sample(0x13, bytes.fromhex('11223344') + bytes(range(20)), timestamp=0).packet.payload.hex()
    spans = {span['path']: span for span in inspect_payload(payload, 'dk32s')['ranges']}
    assert spans['fields.filter_address']['offset'] == 9
    assert spans['fields.filter_address']['raw_hex'] == '11223344'
    assert spans['fields.channel_indices.19']['offset'] == 32
    payload = sample(0xF2, struct.pack('<f', .125) + b'\x07ABC', need_ack=True).packet.payload.hex()
    spans = {span['path']: span for span in inspect_payload(payload, 'datalink')['ranges']}
    assert spans['fields.sync_time_sec']['offset'] == 5
    assert struct.unpack('<f', bytes.fromhex(spans['fields.sync_time_sec']['raw_hex']))[0] == .125
    assert spans['fields.data_batch_id']['offset'] == 9
    assert spans['fields.stream_data_hex']['raw_hex'] == b'ABC'.hex()


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
async def test_saved_duplex_payloads_decode_without_jsonl_and_keep_attempts_separate(tmp_path, monkeypatch):
    from flrc_modem.sync.transceiver import TDDExchangeResult
    from webui.backend.app.api import router
    from webui.backend.app.storage.db import Database

    database = Database(tmp_path / 'session.db')
    monkeypatch.setattr(router, 'get_db', lambda: database)
    database.insert_run({'id': 'duplex-protocol', 'command_id': 'duplex',
                         'command_args': {'session': True, 'protocol': 'dk32s'}, 'cli_argv': [],
                         'status': 'SUCCEEDED', 'created_at': '2026-10-23T10:00:00Z'})
    tx = sample(0x54, b'\x01', timestamp=123, need_ack=True)
    rx = sample(0x54, is_ack=True)
    exchange = TDDExchangeResult(success=True, tx_payload=tx.packet.payload,
                                tx_hw_time=1.0, tx_slot=3, tx_frequency_hz=2480000000,
                                rx_reply=rx, tx_completed=True, reply_matched=True)
    database.insert_event('duplex-protocol', 1, 'duplex.result', None, 'now',
                          {'request_id': 'old-request', 'status': 'reply_matched', **exchange.to_dict()})
    database.restart_run({**database.get_run('duplex-protocol'), 'attempt_created_at': 'later'})
    database.insert_event('duplex-protocol', 1, 'duplex.result', None, 'later',
                          {'request_id': 'new-request', 'status': 'reply_timeout', 'tx_payload_hex': tx.packet.payload.hex()})
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        latest = await client.get('/api/v1/runs/duplex-protocol/session?attempt=2')
        assert latest.status_code == 200
        assert latest.json()['results'][0]['request_id'] == 'new-request'
        assert 'rx_reply' not in latest.json()['results'][0]
        history = await client.get('/api/v1/runs/duplex-protocol/session?attempt=1')
        assert history.status_code == 200
        result = history.json()['results'][0]
        assert result['request_id'] == 'old-request'
        for payload in (result['tx_payload_hex'], result['rx_reply']['payload_hex']):
            parsed = await client.post('/api/v1/protocols/decode', json={'protocol': 'dk32s', 'payload_hex': payload})
            assert parsed.status_code == 200 and parsed.json()['status'] == 'parsed'
            assert parsed.json()['payload_hex'] == payload
            assert parsed.json()['physical_crc_status'] == 'not_checked'
    assert not list(tmp_path.glob('*.jsonl'))


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
