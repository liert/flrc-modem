import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

async function loadModule(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}

const {
  applyCommandTemplate,
  templateSlot,
  resetPhysicalOverrides,
  templateParameterValue,
  isBasicParameter,
  partitionCommandParameters,
  isParameterCustomized,
} = await loadModule('src/utils/commandParameters.ts');
const { commandDraftParameters } = await loadModule('src/utils/commandDraft.ts');
const { cliPreview } = await loadModule('src/utils/runtime.ts');
const { CommandTemplateConfiguration } = await loadModule('src/components/CommandTemplateConfiguration.tsx');

test('模板占位分别显示采集中心、全部目标频点和候选 CRC，保留零值', () => {
  const parsed = { demod: { center_frequency: 2403000000, frequencies: [2399380000, 2404380000],
    crc_initials: [0, 0xffffffff], bt: 0, overlap_seconds: .01 }, transmit: { frequency: 2400000000 } };
  for (const id of ['demod', 'live']) {
    assert.equal(templateParameterValue(id, parsed, 'center_frequency'), 2403000000);
    assert.equal(templateParameterValue(id, parsed, 'frequency'), '2399380000, 2404380000');
    assert.equal(templateParameterValue(id, parsed, 'crc_seed'), '0x0, 0xffffffff');
    assert.equal(templateParameterValue(id, parsed, 'bt'), 0);
    assert.equal(templateParameterValue(id, parsed, 'overlap_ms'), 10);
  }
  assert.equal(templateParameterValue('transmit', parsed, 'frequency'), 2400000000);
  assert.equal(templateParameterValue('demod', { center_frequency: 2403000000 }, 'center_frequency'), 2403000000);
});

test('应用和重置模板清除中心频率、多频点及滤波覆盖，保留硬件与输出路径', () => {
  const original = { config: 'profile.toml', center_frequency: 2480000000, frequency: '2480060000, 2480500000',
    sample_rate: 8000000, filter_cutoff: 350000, crc_seed: '0', allow_frequency_wrap: true,
    serial: 'radio', output: 'outputs/keep.jsonl' };
  const cleared = resetPhysicalOverrides(original);
  for (const name of ['center_frequency', 'frequency', 'sample_rate', 'filter_cutoff', 'crc_seed', 'allow_frequency_wrap']) {
    assert.equal(cleared[name], '');
  }
  for (const name of ['config', 'serial', 'output']) assert.equal(cleared[name], original[name]);
  assert.equal(original.center_frequency, 2480000000);
});

test('多频点编辑草稿生成重复 CLI 参数，预览与提交一致且不改变输入文本', () => {
  const command = { id: 'demod', subcommand: 'demod', parameters: [
    { name: 'center_frequency', type: 'number', cli_flag: '--center-frequency' },
    { name: 'frequency', type: 'list_number', cli_flag: '--frequency' },
    { name: 'crc_seed', type: 'list_string', cli_flag: '--crc-seed' },
  ] };
  const input = { center_frequency: 2403000000, frequency: '2402500000， 2403500000, ', crc_seed: '0, 0xffffffff' };
  const values = commandDraftParameters(command, input, '');
  assert.deepEqual(values.frequency, ['2402500000', '2403500000']);
  assert.deepEqual(values.crc_seed, ['0', '0xffffffff']);
  assert.equal(input.frequency, '2402500000， 2403500000, ');
  assert.equal(cliPreview(command, values), 'python -m flrc_modem demod --center-frequency 2403000000 --frequency 2402500000 --frequency 2403500000 --crc-seed 0 --crc-seed 0xffffffff --ui-events ndjson');
  assert.equal(cliPreview(command, input), cliPreview(command, values));
  assert.equal(commandDraftParameters(command, { frequency: '，  ' }, '').frequency, '');
});

test('原始载荷文件覆盖默认载荷，常驻会话清除文件，JSON 模板供 exchange 使用', () => {
  for (const id of ['modulate', 'transmit', 'duplex']) {
    const values = commandDraftParameters({ id, parameters: [] }, {
      payload_file: 'payload.bin', payload_hex: 'default', message_file: 'default.json', protocol: 'dk32s',
    }, '');
    assert.equal(values.payload_hex, undefined);
    assert.equal(values.message_file, undefined);
    assert.equal(values.payload_file, 'payload.bin');
    assert.equal(values.protocol, id === 'transmit' ? undefined : 'dk32s');
  }
  const session = commandDraftParameters({ id: 'duplex', parameters: [] }, { session: true, payload_file: 'payload.bin' }, '');
  assert.equal(session.payload_file, undefined);
  const values = commandDraftParameters({ id: 'exchange', parameters: [] }, { message_file: 'reply.json' }, '');
  assert.equal(values.message_file, 'reply.json');
});

test('live 最长时长覆盖默认 duration，自定义解析器覆盖默认协议', () => {
  const command = { id: 'live', parameters: [] };
  const values = commandDraftParameters(command, { max_seconds: 0, duration: 10, parser: 'custom:parse', protocol: 'dk32s' }, '');
  assert.equal(values.max_seconds, 0);
  assert.equal(values.duration, undefined);
  assert.equal(values.protocol, undefined);
  assert.equal(values.parser, 'custom:parse');
});

test('物理参数、跳频配置和报文模板可同时选择，切换与清除不覆盖其他角色', () => {
  const physical = { category: 'physical_config', format: 'toml', path: 'templates/physical/dk32s_duplex.toml' };
  const radio = { category: 'rf_config', format: 'json', path: 'templates/rf/device.json' };
  const message = { category: 'message_payload', format: 'json', path: 'templates/messages/query.json' };
  assert.equal(templateSlot(physical), 'config');
  assert.equal(templateSlot(radio), 'rf_config');
  assert.equal(templateSlot(message), 'message_file');
  for (const order of [[physical, radio, message], [message, physical, radio], [radio, message, physical]]) {
    let values = { bit_rate: 650000, payload_hex: 'old' };
    for (const template of order) values = applyCommandTemplate(values, template);
    assert.equal(values.config, physical.path);
    assert.equal(values.rf_config, radio.path);
    assert.equal(values.message_file, message.path);
    assert.equal(values.payload_hex, '');
    const changed = applyCommandTemplate(values, { ...physical, path: 'templates/physical/other.toml' });
    assert.equal(changed.rf_config, radio.path);
    assert.equal(changed.message_file, message.path);
    assert.equal(changed.bit_rate, '');
    const cleared = { ...changed, config: '' };
    assert.equal(cleared.rf_config, radio.path);
    assert.equal(cleared.message_file, message.path);
  }
});

test('三种模板在同一条 CLI 中分别传递，常驻会话保留物理和跳频配置', () => {
  const command = { id: 'duplex', subcommand: 'duplex', parameters: [
    { name: 'config', type: 'path', cli_flag: '--config' },
    { name: 'rf_config', type: 'path', cli_flag: '--rf-config' },
    { name: 'message_file', type: 'path', cli_flag: '--message-file' },
    { name: 'session', type: 'boolean', cli_flag: '--session' },
  ] };
  const input = { config: 'templates/physical/a.toml', rf_config: 'templates/rf/a.json', message_file: 'templates/messages/a.json' };
  assert.equal(cliPreview(command, commandDraftParameters(command, input, '')), 'python -m flrc_modem duplex --config templates/physical/a.toml --rf-config templates/rf/a.json --message-file templates/messages/a.json --ui-events ndjson');
  const session = commandDraftParameters(command, { ...input, session: true }, '');
  assert.equal(session.config, input.config);
  assert.equal(session.rf_config, input.rf_config);
  assert.equal(session.message_file, undefined);
});

test('同步收发表单渲染三个独立选择框，射频 JSON 不进入报文选项', () => {
  const templates = [
    { category: 'physical_config', format: 'toml', name: 'physical/a.toml', path: 'templates/physical/a.toml', title: '物理', applicable_commands: ['duplex'] },
    { category: 'rf_config', format: 'json', name: 'rf/a.json', path: 'templates/rf/a.json', title: '跳频', applicable_commands: ['duplex'] },
    { category: 'message_payload', format: 'json', name: 'messages/a.json', path: 'templates/messages/a.json', title: '报文', applicable_commands: ['duplex'] },
  ];
  const command = { id: 'duplex', parameters: ['config', 'rf_config', 'message_file'].map(name => ({ name })) };
  const html = renderToStaticMarkup(React.createElement(CommandTemplateConfiguration, {
    command, templates, parameters: { config: templates[0].path, rf_config: templates[1].path, message_file: templates[2].path },
    onApply() {}, onChange() {}, onManage() {}, onReset() {},
  }));
  for (const [index, name] of ['config', 'rf_config', 'message_file'].entries()) {
    const select = html.match(new RegExp(`<select[^>]*id="template-${name}"[\\s\\S]*?</select>`))?.[0];
    assert.ok(select);
    assert.ok(select.includes(`value="${templates[index].path}" selected=""`));
    for (const template of templates.filter((_, other) => index !== other)) assert.ok(!select.includes(template.path));
  }
});

test('参数划分正确将必选和基础参数展示、其余划分到高级设置', () => {
  const demodParams = [
    { name: 'iq', required: true, type: 'path' },
    { name: 'sample_rate', required: false, type: 'number' },
    { name: 'frequency', required: false, type: 'list_number' },
    { name: 'bit_rate', required: false, type: 'choice' },
    { name: 'output', required: false, type: 'path' },
    { name: 'center_frequency', required: false, type: 'number' },
    { name: 'filter_cutoff', required: false, type: 'number' },
    { name: 'chunk_seconds', required: false, type: 'number' },
    { name: 'overlap_ms', required: false, type: 'number' },
    { name: 'allow_frequency_wrap', required: false, type: 'boolean' },
    { name: 'crc_seed', required: false, type: 'list_string' },
  ];
  const { basic, advanced } = partitionCommandParameters('demod', demodParams);
  assert.deepEqual(basic.map(p => p.name), ['iq', 'sample_rate', 'frequency', 'bit_rate', 'output']);
  assert.deepEqual(advanced.map(p => p.name), ['center_frequency', 'filter_cutoff', 'chunk_seconds', 'overlap_ms', 'allow_frequency_wrap', 'crc_seed']);

  // rx command
  const rxParams = [
    { name: 'frequency', required: true, type: 'number' },
    { name: 'duration', required: true, type: 'number' },
    { name: 'output', required: true, type: 'path' },
    { name: 'sdr', required: false, type: 'choice' },
    { name: 'sample_rate', required: false, type: 'number' },
    { name: 'gain', required: false, type: 'number' },
    { name: 'master_clock_rate', required: false, type: 'number' },
    { name: 'bandwidth_hz', required: false, type: 'number' },
    { name: 'queue_depth', required: false, type: 'integer' },
  ];
  const rxPartition = partitionCommandParameters('rx', rxParams);
  assert.deepEqual(rxPartition.basic.map(p => p.name), ['frequency', 'duration', 'output', 'sdr', 'sample_rate', 'gain']);
  assert.deepEqual(rxPartition.advanced.map(p => p.name), ['master_clock_rate', 'bandwidth_hz', 'queue_depth']);

  // live command keeps lna_gain, vga_gain, amp in basic
  const liveParams = [
    { name: 'frequency', required: false, type: 'number' },
    { name: 'lna_gain', required: false, type: 'integer' },
    { name: 'amp', required: false, type: 'boolean' },
    { name: 'filter_cutoff', required: false, type: 'number' },
  ];
  const livePartition = partitionCommandParameters('live', liveParams);
  assert.deepEqual(livePartition.basic.map(p => p.name), ['frequency', 'lna_gain', 'amp']);
  assert.deepEqual(livePartition.advanced.map(p => p.name), ['filter_cutoff']);

  // transmit (盲发): frequency, sample_rate, sync, tx_gain 在基础；bit_rate, cr, bt 移动到高级参数
  const transmitParams = [
    { name: 'frequency', required: true, type: 'integer' },
    { name: 'sample_rate', required: true, type: 'number' },
    { name: 'sync', required: true, type: 'string' },
    { name: 'tx_gain', required: false, type: 'integer', default: 40 },
    { name: 'repeat', required: false, type: 'integer' },
    { name: 'protocol', required: false, type: 'choice' },
    { name: 'bit_rate', required: false, type: 'choice' },
    { name: 'cr', required: false, type: 'choice' },
    { name: 'bt', required: false, type: 'choice' },
    { name: 'ramp_time_us', required: false, type: 'choice' },
  ];
  const transmitPartition = partitionCommandParameters('transmit', transmitParams);
  assert.deepEqual(transmitPartition.basic.map(p => p.name), ['frequency', 'sample_rate', 'sync', 'tx_gain', 'repeat', 'protocol']);
  assert.deepEqual(transmitPartition.advanced.map(p => p.name), ['bit_rate', 'cr', 'bt', 'ramp_time_us']);

  // 同步收发：收发极性反转属于基础参数，回复极性可按每条报文覆盖
  const duplexParams = [
    { name: 'frequency', required: false, type: 'integer' },
    { name: 'sync', required: false, type: 'string' },
    { name: 'tx_invert_sync', required: false, type: 'boolean' },
    { name: 'reply_invert_sync', required: false, type: 'boolean' },
    { name: 'master_clock_rate', required: false, type: 'choice' },
  ];
  const duplexPartition = partitionCommandParameters('duplex', duplexParams);
  assert.deepEqual(duplexPartition.basic.map(p => p.name), ['frequency', 'sync', 'tx_invert_sync', 'reply_invert_sync']);
  assert.deepEqual(duplexPartition.advanced.map(p => p.name), ['master_clock_rate']);
});

test('isParameterCustomized 正确识别用户自定义或非默认参数', () => {
  assert.equal(isParameterCustomized({ name: 'filter_cutoff', type: 'number', default: null }, ''), false);
  assert.equal(isParameterCustomized({ name: 'filter_cutoff', type: 'number', default: null }, undefined), false);
  assert.equal(isParameterCustomized({ name: 'filter_cutoff', type: 'number', default: 350000 }, 350000), false);
  assert.equal(isParameterCustomized({ name: 'filter_cutoff', type: 'number', default: 350000 }, 500000), true);
  assert.equal(isParameterCustomized({ name: 'amp', type: 'boolean', default: false }, false), false);
  assert.equal(isParameterCustomized({ name: 'amp', type: 'boolean', default: false }, true), true);
  assert.equal(isParameterCustomized({ name: 'crc_seed', type: 'list_string', default: [] }, ['0x0']), true);
  assert.equal(isParameterCustomized({ name: 'crc_seed', type: 'list_string', default: [] }, []), false);
});
