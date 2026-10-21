"""分层模板、子目录 API 与同步收发配置一致性；不启动硬件。"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.encoding import EncodeContext, MessageTemplate, load_encoder
from flrc_modem.encoding.input import read_message
from flrc_modem.duplex_cli import main, make_argument_parser, resolve_configuration
from flrc_modem.sync.live import resolve_rf_settings
from flrc_modem.templates import resolve_template_path
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.main import app
from webui.backend.app.templates.manager import TemplateManager

ROOT = Path(__file__).resolve().parents[1]
PHYSICAL = ROOT / 'templates/physical/dk32s.toml'
RF = ROOT / 'templates/rf/current-rf-config-20261020.json'
MESSAGE = ROOT / 'templates/messages/dk32s_tx_get_hops.json'


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_repository_templates_share_metadata_and_are_separated_by_role():
    templates = TemplateManager().list_templates()
    assert {'physical_config', 'rf_config', 'message_payload'} == {item.category for item in templates}
    folders = {'physical_config': 'physical', 'rf_config': 'rf', 'message_payload': 'messages'}
    for item in templates:
        assert item.name.startswith(folders[item.category] + '/')
        metadata = item.parsed['template']
        assert metadata['schema_version'] == 1 and metadata['kind'] == item.category
        assert all(metadata[key] for key in ('title', 'description', 'protocol'))
        if item.category == 'message_payload':
            original = read_message(Path(item.path))
            bare = {key: value for key, value in original.items() if key != 'template'}
            encoder = load_encoder(metadata['protocol'])
            context = EncodeContext(0, 0, 2500000)
            assert MessageTemplate(encoder, original).encode(context) == MessageTemplate(encoder, bare).encode(context)


def test_recursive_templates_and_unique_old_names_and_traversal(tmp_path):
    manager = TemplateManager(tmp_path / 'templates')
    manager.save_template('messages/a/same.json', '{"command":"0x23"}')
    manager.save_template('messages/b/same.json', '{"command":"0x22"}')
    assert {item.name for item in manager.list_templates()} == {'messages/a/same.json', 'messages/b/same.json'}
    assert manager.get_template('same.json') is None
    assert manager.get_template('messages/a/same.json').parsed['command'] == '0x23'
    manager.save_template('physical/unique.toml', '[demod]\nbit_rate=325000\n')
    assert manager.get_template('unique.toml').name == 'physical/unique.toml'
    assert resolve_template_path(tmp_path / 'templates/unique.toml') == tmp_path / 'templates/physical/unique.toml'
    for name in ('../outside.json', 'messages/../../outside.json', str(tmp_path / 'outside.json')):
        with pytest.raises(ValueError):
            manager.save_template(name, '{}')


@pytest.mark.anyio
async def test_subdirectory_templates_api_read_edit_and_invalid_metadata(tmp_path, monkeypatch):
    from webui.backend.app.templates import manager as module
    monkeypatch.setattr(module, '_TEMPLATE_MGR', TemplateManager(tmp_path / 'templates'))
    content = {'template': {'schema_version': 1, 'kind': 'rf_config', 'title': '射频'},
               'hopping_table': [2403000000, 2404000000], 'sync_word': '54455354'}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/templates/rf/device.json', json={'content': json.dumps(content)})
        assert response.status_code == 200, response.text
        assert response.json()['category'] == 'rf_config'
        assert response.json()['name'] == 'rf/device.json'
        response = await client.get('/api/v1/templates/rf%2Fdevice.json')
        assert response.status_code == 200
        assert response.json()['parsed']['hopping_table'] == [2403000000, 2404000000]
        content['template']['kind'] = 'message_payload'
        response = await client.post('/api/v1/templates/rf/device.json', json={'content': json.dumps(content)})
        assert response.status_code == 400
        assert (await client.get('/api/v1/templates/rf/device.json')).json()['category'] == 'rf_config'

        # 测试删除模板
        del_resp = await client.delete('/api/v1/templates/rf/device.json')
        assert del_resp.status_code == 200
        assert del_resp.json()['deleted'] is True
        assert (await client.get('/api/v1/templates/rf/device.json')).status_code == 404
        assert (await client.delete('/api/v1/templates/rf/device.json')).status_code == 404


def test_three_templates_reach_preflight_and_real_duplex_configuration(monkeypatch):
    values = dict(config=str(PHYSICAL), rf_config=str(RF), message_file=str(MESSAGE), no_wait=True, json=True)
    argv = CommandRegistry().build_cli_argv('duplex', values)
    planned = preflight('duplex', argv)
    args = make_argument_parser().parse_args(argv[4:])
    profile, rf = resolve_configuration(args)
    assert rf['source'] == str(RF)
    assert profile.sync == planned['sync'] == 'f264709b'
    assert planned['frequency'] == rf['frequency'] == rf['hopping_table'][0] == 2419260000
    assert planned['sample_rate'] == profile.sample_rate == 2500000
    assert len(rf['hopping_table']) == 20
    radio = MagicMock()
    radio.return_value.send_and_wait_reply.return_value = SimpleNamespace(success=True, to_dict=lambda: {})
    monkeypatch.setattr('flrc_modem.duplex_cli.LiveTDDTransceiver', radio)
    assert main(argv[4:]) == 0
    effective = radio.call_args.kwargs
    assert effective['sync_word'] == planned['sync']
    assert effective['frequency_hz'] == planned['frequency']
    assert effective['hopping_table'] == planned['rf_config']['hopping_table']
    assert effective['bit_rate'] == planned['bit_rate']


def test_explicit_overrides_and_old_paths_keep_working():
    values = dict(config=str(ROOT / 'templates/dk32s.toml'),
                  rf_config=str(ROOT / 'templates/current-rf-config-20261020.json'),
                  message_file=str(ROOT / 'templates/dk32s_tx_get_hops.json'),
                  frequency=2465660000, sync='54455354', bt=0)
    planned = preflight('duplex', CommandRegistry().build_cli_argv('duplex', values))
    assert planned['frequency'] == 2465660000 and planned['sync'] == '54455354' and planned['bt'] == 0
    assert planned['rf_config']['source'].replace('\\', '/').endswith('templates/rf/current-rf-config-20261020.json')


@pytest.mark.parametrize('content', ['{}', '{', '[]', '{"hopping_table":[]}',
    '{"hopping_table":[true]}', '{"hopping_table":[2403000000.5]}',
    '{"hopping_table":[2403000000],"sync_word":"bad"}'])
def test_invalid_rf_never_silently_uses_historical_table(tmp_path, content):
    path = tmp_path / 'invalid.json'
    path.write_text(content)
    with pytest.raises(ValueError, match='射频配置'):
        resolve_rf_settings(path)
    with pytest.raises(ValueError, match='射频配置'):
        preflight('duplex', CommandRegistry().build_cli_argv('duplex', dict(rf_config=str(path), session=True)))


def test_missing_file_and_unknown_table_are_distinguished(tmp_path):
    with pytest.raises(ValueError, match='射频配置'):
        resolve_rf_settings(tmp_path / 'missing.json')
    default = resolve_rf_settings()
    assert default['source'] == 'builtin_default'
    assert default['sync'] is None and default['warnings']
