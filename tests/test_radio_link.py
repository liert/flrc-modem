"""The linked live session must retain PHY and hopping configuration."""
import json
import pytest

from webui.backend.app.commands.registry import get_command_registry
from webui.backend.app.commands.preflight import preflight
from flrc_modem.duplex_cli import make_argument_parser, resolve_configuration


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


def test_live_session_resolves_custom_hopping_table(tmp_path):
    table = [2411000000, 2444000000, 2477000000]
    path = tmp_path / 'current radio.json'
    path.write_text(json.dumps({'hopping_table': table}), encoding='utf-8')
    argv = get_command_registry().build_cli_argv('duplex', {'session': True, 'rf_config': str(path)})
    result = preflight('duplex', argv)
    assert result['rf_config']['hopping_table'] == table


def test_invalid_hopping_config_does_not_silently_fall_back(tmp_path):
    argv = get_command_registry().build_cli_argv('duplex', {
        'session': True, 'rf_config': str(tmp_path / 'missing.json'),
    })
    with pytest.raises(ValueError):
        preflight('duplex', argv)
