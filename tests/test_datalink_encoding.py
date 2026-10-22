"""Known radio bytes and template integration; never transmits RF."""
import json
from pathlib import Path

import pytest

from flrc_modem.encoding import EncodeContext, MessageTemplate, load_encoder
from flrc_modem.parsing.api import load_parser
from flrc_modem.phy.encoder import encode_packet
from test_protocols import frame_from_payload
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.templates.manager import TemplateManager

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = EncodeContext(0, 0, 2_000_000)


@pytest.mark.parametrize('command,fields,expected', [
    ('0x18', {'mode': 1, 'sync_count': 1}, '070201001801011d'),
    ('0xF3', {'fcu_type': 0}, '06010100f300f5'),
    ('0xF7', {'baud_rate': 115200}, '06010100f705fe'),
    ('0xF7', {'baud_index': 5}, '06010100f705fe'),
])
def test_downlink_known_bytes_and_parser(command, fields, expected):
    message = {'direction': 'ground_to_air', 'command': command, 'need_ack': False,
               'is_ack': True, 'fields': fields}
    payload = MessageTemplate(load_encoder('datalink'), message).encode(CONTEXT)
    assert payload.hex() == expected
    result = load_parser('datalink')(frame_from_payload(payload))
    assert result['direction'] == 'ground_to_air' and result['sum8_ok']
    assert result['sequence'] == 0 and result['is_ack'] and not result['need_ack']


def test_recorded_bidirectional_radio_fixtures_keep_wire_layout_and_crc():
    for fixture in json.loads((ROOT / 'tests/fixtures/datalink_packets.json').read_text()):
        payload = bytes.fromhex(fixture['payload_hex'])
        message = {'direction': fixture['direction'], 'command': '0xF2', 'sequence': fixture['sequence'],
                   'need_ack': fixture['direction'] == 'air_to_ground',
                   'is_ack': fixture['direction'] == 'ground_to_air',
                   'fields': {'data_hex': payload[5:-1].hex()}}
        encoded = MessageTemplate(load_encoder('datalink'), message).encode(CONTEXT)
        assert encoded == payload
        assert encode_packet(encoded, bytes.fromhex(fixture['sync_hex'])).crc.hex() == fixture['crc_hex']


@pytest.mark.parametrize('overrides', [
    {'need_ack': True}, {'is_ack': False}, {'direction': 'air_to_ground'}, {'timestamp_ticks': 0},
    {'fields': {'baud_index': 7}}, {'fields': {'baud_rate': 12345}},
    {'fields': {'baud_index': True}}, {'fields': {'baud_rate': 115200, 'baud_index': 5}},
    {'fields': {'baud_rate': 115200, 'typo': 1}},
])
def test_invalid_downlink_settings_are_rejected(overrides):
    message = {'direction': 'ground_to_air', 'command': '0xF7', 'need_ack': False, 'is_ack': True,
               'fields': {'baud_rate': 115200}, **overrides}
    with pytest.raises(ValueError):
        MessageTemplate(load_encoder('datalink'), message).encode(CONTEXT)


def test_sequence_changes_without_adding_a_dk32s_timestamp():
    message = {'direction': 'ground_to_air', 'command': '0xF3', 'is_ack': True, 'fields': {'fcu_type': 8}}
    template = MessageTemplate(load_encoder('datalink'), message)
    for index in (0, 1, 32767, 32768):
        payload = template.encode(EncodeContext(index, 1000000, 2000000))
        assert len(payload) == 7
        result = load_parser('datalink')(frame_from_payload(payload))
        assert result['sequence'] == index % 32768 and result['fields']['fcu_type'] == 8


def test_templates_select_datalink_for_blind_transmit_and_preflight():
    manager, registry = TemplateManager(), CommandRegistry()
    for name, size in (('sync_mode', 8), ('fcu_type', 7), ('baud_rate', 7)):
        template = manager.get_template(f'messages/datalink_tx_{name}.json')
        assert template.parsed['template']['protocol'] == 'datalink'
        assert template.applicable_commands == ['transmit', 'exchange']
        argv = registry.build_cli_argv('transmit', {
            'message_file': template.path, 'protocol': 'datalink', 'frequency': 2480000000,
            'sample_rate': 2000000, 'sync': '183a1d2f', 'config': 'templates/physical/datalink.toml',
        })
        assert preflight('transmit', argv)['payload_bytes'] == size
