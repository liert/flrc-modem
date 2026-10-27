import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { create, act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { writeFile, unlink, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const bundled = await build({
  stdin: { contents: `
    export * from './src/utils/physicalTemplate.ts';
    export { PhysicalTemplateFields } from './src/components/PhysicalTemplateFields.tsx';
    export { TemplateLibraryView } from './src/views/TemplateLibraryView.tsx';
    export { api } from './src/api/client.ts';
    export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
  `, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'zustand', 'lucide-react'],
});
const target = join(process.cwd(), 'node_modules', `.physical-template-${process.pid}.mjs`);
let loaded;
try {
  await writeFile(target, bundled.outputFiles[0].text);
  loaded = await import(pathToFileURL(target).href);
} finally { await unlink(target); }
const { patchTomlSection, readPhysicalParameters, changePhysicalParameter, PhysicalTemplateFields,
  TemplateLibraryView, api, useWorkstationStore, PHYSICAL_BIT_RATES, PHYSICAL_CODING_RATES,
  PHYSICAL_BT, PHYSICAL_HEADERS, PHYSICAL_CRC_BYTES } = loaded;
const original = await readFile('../../templates/physical/dk32s.toml', 'utf8');

test('参数选择只更新 demod 字段，保留注释、同步字及其他段落', () => {
  const source = original + '\nsync_word = \"b42b2bb4\" # keep sync\n\n[modulate]\nbt = 0.5\n';
  const result = changePhysicalParameter(source, 'bt', '0');
  assert.equal(readPhysicalParameters(result).bt, 0);
  assert.ok(result.includes('# DK32S 物理层参数预设'));
  assert.ok(result.includes('sync_word = \"b42b2bb4\" # keep sync'));
  assert.ok(result.endsWith('[modulate]\nbt = 0.5\n'));
  assert.equal(result.replace(/bt = 0(?=\r?\n)/, 'bt = 1.0'), source);
});

test('高码率选择提高不足的模板采样率并设置对应滤波预设', () => {
  const source = patchTomlSection(original, 'demod', { sample_rate: '2500000.0' });
  const result = changePhysicalParameter(source, 'bit_rate', '1300000');
  assert.equal(readPhysicalParameters(result).bit_rate, 1300000);
  assert.match(result, /sample_rate = 5200000/);
  assert.match(result, /filter_cutoff = 750000/);
  assert.match(changePhysicalParameter(result, 'bit_rate', '325000'), /sample_rate = 5200000/);
});

test('CRC 位宽改变时重置初值并清除多行候选数组，不误改其他段落', () => {
  const source = '[demod]\r\ncrc_bytes = 4 # width\r\ncrc_initial = 0xFFFFFFFF\r\ncrc_initials = [\r\n  0xFFFFFFFF, # ] inside comment\r\n  0,\r\n]\r\n# retain\r\n\r\n[other]\r\ncrc_initials = [123]\r\n';
  const result = changePhysicalParameter(source, 'crc_bytes', '2');
  assert.match(result, /crc_bytes = 2 # width/);
  assert.match(result, /crc_initial = 0xFFFF\r\n/);
  assert.ok(!result.includes('0xFFFFFFFF'));
  assert.ok(result.includes('# retain\r\n'));
  assert.ok(result.endsWith('[other]\r\ncrc_initials = [123]\r\n'));
});

test('标题和描述安全转义，且不重置已选择的物理字段或高级内容', () => {
  const source = changePhysicalParameter(original, 'coding_rate', '1/2');
  const result = patchTomlSection(source, 'template', { title: JSON.stringify('测试 \"A\"\n第二行') });
  assert.equal(readPhysicalParameters(result).coding_rate, '1/2');
  assert.ok(result.includes('title = ' + JSON.stringify('测试 \"A\"\n第二行')));
  assert.match(result, /crc_initial = 0xFFFFFF/);
});

test('不把多行字符串中的伪表头或注释当作可修改参数', () => {
  const source = '[template]\ndescription = \"\"\"example\n[demod]\nbt = 0\n\"\"\"\n[demod]\nbt = 1 # keep\n';
  const result = changePhysicalParameter(source, 'bt', '0.5');
  assert.ok(result.includes('[demod]\nbt = 0\n\"\"\"'));
  assert.ok(result.endsWith('bt = 0.5 # keep\n'));
  assert.throws(() => readPhysicalParameters('[demod]\ncrc_initials = [1,'), /补全/);
  assert.throws(() => changePhysicalParameter(original, 'bt', '2'), /不支持/);
  assert.throws(() => readPhysicalParameters('demod = { bt = 1 }'), /行内表/);
});

test('所有支持的选项都可组合生成，默认值与实际设备模板一致', () => {
  assert.deepEqual(readPhysicalParameters(original), { bit_rate: 325000, coding_rate: '3/4',
    bt: 1, header_type: 'variable', crc_bytes: 3 });
  let count = 0;
  for (const rate of PHYSICAL_BIT_RATES) for (const cr of PHYSICAL_CODING_RATES)
    for (const bt of PHYSICAL_BT) for (const header of PHYSICAL_HEADERS) for (const crc of PHYSICAL_CRC_BYTES) {
      let source = original;
      for (const [key, value] of Object.entries({ bit_rate: rate, coding_rate: cr, bt, header_type: header, crc_bytes: crc }))
        source = changePhysicalParameter(source, key, String(value));
      assert.deepEqual(readPhysicalParameters(source), { bit_rate: rate, coding_rate: cr, bt, header_type: header, crc_bytes: crc });
      count++;
    }
  assert.equal(count, 486);
});

function nodeText(node) {
  if (Array.isArray(node)) return node.map(nodeText).join('');
  return typeof node === 'string' ? node : (node?.children || []).map(nodeText).join('');
}
const pause = () => new Promise(resolve => setTimeout(resolve, 20));

test('物理参数表单与手动源码双向同步，非法源码可恢复', async () => {
  let renderer;
  function Harness() {
    const [content, setContent] = React.useState(original);
    return React.createElement(React.Fragment, null,
      React.createElement(PhysicalTemplateFields, { content, onChange: setContent }),
      React.createElement('textarea', { value: content, onChange: event => setContent(event.target.value) }));
  }
  await act(async () => { renderer = create(React.createElement(Harness)); });
  try {
    assert.equal(renderer.root.findAllByType('select').length, 5);
    await act(async () => renderer.root.findByProps({ 'aria-label': '编码率' }).props.onChange({ target: { value: '1' } }));
    assert.match(renderer.root.findByType('textarea').props.value, /coding_rate = \"1\"/);
    const content = renderer.root.findByType('textarea').props.value.replace('bt = 1.0', 'bt = 0.5');
    await act(async () => renderer.root.findByType('textarea').props.onChange({ target: { value: content } }));
    assert.equal(renderer.root.findByProps({ 'aria-label': 'BT' }).props.value, '0.5');
    await act(async () => renderer.root.findByType('textarea').props.onChange({ target: { value: '[demod]\nbt = \"' } }));
    assert.equal(renderer.root.findAllByType('select').length, 0);
    assert.match(nodeText(renderer.toJSON()), /参数选择暂不可用/);
    await act(async () => renderer.root.findByType('textarea').props.onChange({ target: { value: original } }));
    assert.equal(renderer.root.findAllByType('select').length, 5);
  } finally { await act(async () => renderer.unmount()); }
});

test('新建物理模板实际保存选择值，标题修改保留配置，其他分类不显示物理表单', async () => {
  const oldWindow = globalThis.window, oldDocument = globalThis.document;
  const oldApi = { ...api }, oldStore = useWorkstationStore.getState();
  const template = { name: 'physical/dk32s.toml', path: 'templates/physical/dk32s.toml',
    title: 'DK32S', description: 'test', format: 'toml', category: 'physical_config',
    applicable_commands: ['demod'], raw_content: original, parsed: { template: { protocol: 'dk32s' }, demod: {} } };
  const saved = [];
  let templates = [template];
  api.getTemplates = async () => templates;
  api.saveTemplate = async (name, content) => {
    saved.push({ name, content });
    const item = { ...template, name, path: 'templates/' + name, raw_content: content };
    templates = [...templates.filter(item => item.name !== name), item];
    return item;
  };
  globalThis.window = { addEventListener() {}, removeEventListener() {}, matchMedia: () => ({ matches: false }) };
  globalThis.document = { activeElement: null };
  useWorkstationStore.setState({ selectedTemplate: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  let renderer;
  const click = async label => {
    let button;
    for (let attempt = 0; attempt < 25; attempt++) {
      button = renderer.root.findAllByType('button').find(node => nodeText(node).trim() === label);
      if (button) break;
      await act(async () => { await pause(); });
    }
    assert.ok(button, 'Missing button: ' + label + '; rendered: ' + nodeText(renderer.toJSON()).slice(0, 1800));
    await act(async () => { button.props.onClick(); await pause(); });
  };
  try {
    await act(async () => { renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(TemplateLibraryView))); await pause(); });
    await click('新建');
    const fields = () => renderer.root.findByProps({ 'aria-label': '新建物理参数' });
    for (const [label, value] of [['码率', '1300000'], ['编码率', '1'], ['BT', '0'], ['包头', 'fixed'], ['CRC', '4']])
      await act(async () => fields().findByProps({ 'aria-label': label }).props.onChange({ target: { value } }));
    await act(async () => renderer.root.findByProps({ placeholder: '例如: custom_profile' }).props.onChange({ target: { value: 'my-phy' } }));
    await act(async () => renderer.root.findByProps({ placeholder: '例如: 自定义测试参数' }).props.onChange({ target: { value: '配置 \"A\"' } }));
    await click('创建模板');
    assert.equal(saved.length, 1);
    assert.equal(saved[0].name, 'physical/my-phy.toml');
    assert.deepEqual(readPhysicalParameters(saved[0].content), { bit_rate: 1300000, coding_rate: '1', bt: 0, header_type: 'fixed', crc_bytes: 4 });
    assert.ok(saved[0].content.includes('title = ' + JSON.stringify('配置 \"A\"')));
    assert.doesNotMatch(saved[0].content, /(?:sample_rate|chunk_seconds)\s*=/);
    const editFields = renderer.root.findByProps({ 'aria-label': '物理参数设置' });
    await act(async () => editFields.findByProps({ 'aria-label': 'BT' }).props.onChange({ target: { value: '0.5' } }));
    await click('保存修改');
    assert.equal(readPhysicalParameters(saved[1].content).bt, 0.5);
    await click('新建');
    const category = renderer.root.findAllByType('button').find(node => nodeText(node).startsWith('射频跳频templates/'));
    assert.ok(category);
    await act(async () => category.props.onClick());
    assert.equal(renderer.root.findAllByProps({ 'aria-label': '新建物理参数' }).length, 0);
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    client.clear(); Object.assign(api, oldApi); useWorkstationStore.setState(oldStore, true);
    globalThis.window = oldWindow; globalThis.document = oldDocument;
  }
});
