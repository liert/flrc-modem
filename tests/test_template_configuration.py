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
from flrc_modem.sync.orchestration import validate_plan
from flrc_modem.templates import resolve_template_path
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.main import app
from webui.backend.app.templates.manager import TemplateManager

ROOT = Path(__file__).resolve().parents[1]
PHYSICAL = ROOT / 'templates/physical/dk32s.toml'
RF = ROOT / 'templates/rf/dk32s-rf-config.json'
MESSAGE = ROOT / 'templates/messages/dk32s_tx_get_hops.json'


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_workbench_encodes_all_library_payloads_without_changing_templates():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        templates = (await client.get('/api/v1/templates')).json()
        messages = [item for item in templates if item['category'] == 'message_payload']
        assert messages
        for item in messages:
            response = await client.post('/api/v1/modulate/template-payload',
                                         json={'name': item['name'], 'sample_rate': 2000000})
            assert response.status_code == 200, (item['name'], response.text)
            result = response.json()
            assert result['protocol'] == item['parsed']['template']['protocol']
            payload = bytes.fromhex(result['payload_hex'])
            assert 6 <= len(payload) <= 127
            assert payload == MessageTemplate(load_encoder(result['protocol']), item['parsed']).encode(
                EncodeContext(0, 0, 2000000))
            inspected = await client.post('/api/v1/protocols/decode',
                                          json={'protocol': result['protocol'], 'payload_hex': result['payload_hex']})
            assert inspected.status_code == 200
        assert (await client.get('/api/v1/templates')).json() == templates


@pytest.mark.anyio
async def test_workbench_payload_reloads_saved_template_and_rejects_invalid_input(tmp_path, monkeypatch):
    manager = TemplateManager(tmp_path / 'templates')
    monkeypatch.setattr('webui.backend.app.api.router.get_template_manager', lambda: manager)
    doc = json.loads(MESSAGE.read_text(encoding='utf-8'))
    manager.save_template('messages/nested/custom.json', json.dumps(doc))
    manager.save_template('physical/test.toml', '[demod]\nbit_rate=325000\n')
    endpoint = '/api/v1/modulate/template-payload'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        first = await client.post(endpoint, json={'name': 'messages/nested/custom.json'})
        assert first.status_code == 200
        doc['sequence'] = 42
        manager.save_template('messages/nested/custom.json', json.dumps(doc))
        updated = await client.post(endpoint, json={'name': 'messages/nested/custom.json'})
        assert updated.status_code == 200
        assert first.json()['payload_hex'] != updated.json()['payload_hex']
        for name, status in [('missing.json', 404), ('../outside.json', 400), ('physical/test.toml', 400)]:
            response = await client.post(endpoint, json={'name': name})
            assert response.status_code == status, response.text
        for sample_rate in [0, -1, 'NaN', 'Infinity']:
            response = await client.post(endpoint, json={'name': 'messages/nested/custom.json', 'sample_rate': sample_rate})
            assert response.status_code == 422
        doc['template']['protocol'] = 'custom.module:encode'
        manager.save_template('messages/nested/custom.json', json.dumps(doc))
        assert (await client.post(endpoint, json={'name': 'messages/nested/custom.json'})).status_code == 400
        doc['template']['protocol'] = 'dk32s'
        doc['fields'] = {'unknown_field': 1}
        manager.save_template('messages/nested/custom.json', json.dumps(doc))
        invalid = await client.post(endpoint, json={'name': 'messages/nested/custom.json'})
        assert invalid.status_code == 400 and 'detail' in invalid.json()


def test_repository_templates_share_metadata_and_are_separated_by_role():
    templates = TemplateManager().list_templates()
    assert {'physical_config', 'rf_config', 'message_payload', 'duplex_plan'} == {item.category for item in templates}
    folders = {'physical_config': 'physical', 'rf_config': 'rf', 'message_payload': 'messages', 'duplex_plan': 'plans'}
    for item in templates:
        assert item.name.startswith(folders[item.category] + '/')
        metadata = item.parsed['template']
        assert metadata['schema_version'] == 1 and metadata['kind'] == item.category
        assert all(metadata[key] for key in ('title', 'description', 'protocol'))
        if item.category == 'duplex_plan':
            assert item.applicable_commands == ['duplex']
            assert validate_plan(item.parsed['plan'])['plan_id']
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

        # Plans use the same CRUD API and the runtime validator. Invalid edits
        # must not replace the last usable file.
        plan = {'template': {'schema_version': 1, 'kind': 'duplex_plan', 'title': '自定义流程'},
                'plan': {'plan_id': 'custom', 'steps': [{'id': 'first', 'payload_hex': '010203040506'}]}}
        saved = await client.post('/api/v1/templates/plans/custom.json', json={'content': json.dumps(plan)})
        assert saved.status_code == 200 and saved.json()['category'] == 'duplex_plan'
        listed = (await client.get('/api/v1/templates?command_id=duplex')).json()
        assert any(item['name'] == 'plans/custom.json' and item['title'] == '自定义流程' for item in listed)
        assert (await client.get('/api/v1/templates?command_id=transmit')).json() == []
        plan['plan']['cycles'] = 0
        invalid = await client.post('/api/v1/templates/plans/custom.json', json={'content': json.dumps(plan)})
        assert invalid.status_code == 400
        current = await client.get('/api/v1/templates/plans/custom.json')
        assert 'cycles' not in current.json()['parsed']['plan']
        assert (await client.delete('/api/v1/templates/plans/custom.json')).status_code == 200


def test_three_templates_reach_preflight_and_real_duplex_configuration(monkeypatch):
    values = dict(config=str(PHYSICAL), rf_config=str(RF), message_file=str(MESSAGE), sample_rate=2500000, no_wait=True, json=True)
    argv = CommandRegistry().build_cli_argv('duplex', values)
    planned = preflight('duplex', argv)
    args = make_argument_parser().parse_args(argv[4:])
    profile, rf = resolve_configuration(args)
    snapshot = json.loads(RF.read_text(encoding='utf-8'))
    assert rf['source'] == str(RF)
    # RF snapshots change with the radio; verify source precedence against the
    # selected file instead of pinning values from an older capture.
    assert profile.sync == planned['sync'] == snapshot['sync_word']['ota_sync_word_hex']
    assert planned['frequency'] == rf['frequency'] == rf['hopping_table'][0] == snapshot['hopping_table'][0]['frequency_hz']
    assert planned['sample_rate'] == profile.sample_rate == 2500000
    assert len(rf['hopping_table']) == snapshot['channel_count']
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
                  rf_config=str(ROOT / 'templates/dk32s-rf-config.json'),
                  message_file=str(ROOT / 'templates/dk32s_tx_get_hops.json'),
                  frequency=2465660000, sync='54455354', bt=0)
    planned = preflight('duplex', CommandRegistry().build_cli_argv('duplex', values))
    assert planned['frequency'] == 2465660000 and planned['sync'] == '54455354' and planned['bt'] == 0
    assert planned['rf_config']['source'].replace('\\', '/').endswith('templates/rf/dk32s-rf-config.json')


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
