import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { writeFile, unlink } from 'node:fs/promises';

const bundle = await build({
  stdin: { contents: `
    export { TransmitWorkbenchPanel } from './src/components/TransmitWorkbenchPanel.tsx';
    export { FrameSliceInspector } from './src/components/FrameSliceInspector.tsx';
    export { useRadioWorkbenchStore } from './src/stores/useRadioWorkbenchStore.ts';
    export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
  `, resolveDir: process.cwd(), loader: 'ts' },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'lucide-react'],
});
const target = join(process.cwd(), 'node_modules', `.transmit-workbench-${process.pid}.mjs`);
let modules;
try {
  await writeFile(target, bundle.outputFiles[0].text);
  modules = await import(pathToFileURL(target).href);
} finally { await unlink(target); }
const { TransmitWorkbenchPanel, FrameSliceInspector, useRadioWorkbenchStore: radio, useWorkstationStore: workstation } = modules;

test('切片解析使用当前切片字节；非 Payload 只展示 Hex，切换载荷不会复用旧解析', () => {
  const payload = '06010100f705fe';
  const result = { protocol: 'datalink', payload_hex: payload, payload_length: 7, status: 'parsed', error: null,
    application: { command_hex: '0xF7', command_name: '串口波特率', fields: { baud_rate: 115200 } }, ranges: [] };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['protocolInspection', 'datalink', payload], result);
  const renderSlice = (name, hex = payload) => renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
    React.createElement(FrameSliceInspector, { protocol: 'datalink', slice: { name, hex_value: hex, bits: hex.length * 4,
      bytes: hex.length / 2, description: '', color: 'emerald' } })));
  try {
    const parsed = renderSlice('Payload Data');
    assert.match(parsed, /十六进制视图/);
    assert.match(parsed, /0xF7 · 串口波特率/);
    const outerDetails = parsed.match(/<details[^>]*>/)[0];
    assert.doesNotMatch(outerDetails, /\bopen=/);
    assert.equal((parsed.match(/十六进制视图/g) ?? []).length, 1);
    assert.ok(parsed.indexOf('十六进制视图') < parsed.indexOf('data-byte-offset='));
    const sync = renderSlice('Sync Word');
    assert.match(sync, /十六进制视图/);
    assert.doesNotMatch(sync, /解码字段|串口波特率|正在解析/);
    const pending = renderSlice('Payload Data', '010203040506');
    assert.match(pending, /正在解析载荷字段/);
    assert.doesNotMatch(pending, /串口波特率|115200/);
    const redundancy = renderSlice('FEC Redundancy', '');
    assert.match(redundancy, /没有独立的十六进制字节/);
    assert.doesNotMatch(redundancy, /data-byte-offset|正在解析/);
    client.setQueryData(['protocolInspection', 'datalink', payload], { ...result, status: 'error',
      error: 'sum8 校验失败', application: null });
    const invalid = renderSlice('Payload Data');
    assert.match(invalid, /十六进制视图/);
    assert.match(invalid, /sum8 校验失败/);
    assert.doesNotMatch(invalid, /串口波特率|115200|协议解析/);
  } finally { client.clear(); }
});

function render(mode, { frame = {}, transmit = {}, runId = null } = {}) {
  const radioInitial = radio.getInitialState();
  const workstationInitial = workstation.getInitialState();
  const originalFrame = radioInitial.frame;
  const originalTransmit = radioInitial.transmit;
  const originalRunId = workstationInitial.transmitRunId;
  // SSR reads Zustand's initial snapshot; supply the mode selected by the client.
  radioInitial.frame = { ...originalFrame, ...frame };
  radioInitial.transmit = { ...originalTransmit, ...transmit, mode };
  workstationInitial.transmitRunId = runId;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  try {
    return renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
      React.createElement(TransmitWorkbenchPanel)));
  } finally {
    client.clear();
    radioInitial.frame = originalFrame;
    radioInitial.transmit = originalTransmit;
    workstationInitial.transmitRunId = originalRunId;
  }
}

test('物理帧剖析显示帧切片与两种波形，不出现空口发射或文件导出操作', () => {
  const html = render('preview');
  assert.match(html, /Air-Frame PHY Slices/);
  assert.match(html, /Amplitude Envelope/);
  assert.match(html, /Instantaneous Frequency Deviation/);
  assert.match(html, />协议载荷</);
  assert.match(html, /aria-haspopup="dialog"[^>]*>[\s\S]*?选择模板/);
  assert.doesNotMatch(html, /快速协议载荷模板预设|DK32S 对频握手 \(Pairing\)|FLRC 极限大载荷/);
  assert.doesNotMatch(html, /立即空口发射|生成并导出 IQ 文件|中心发射频率|输出 IQ 文件路径|<code>/);
});

test('实时发射显示射频参数和发射命令，操作位于共享参数之前', () => {
  const html = render('live');
  assert.match(html, /SDR 实时发射控制/);
  assert.match(html, /频率 \(Hz\)/);
  assert.match(html, /立即空口发射/);
  assert.match(html, /<code>flrc transmit /);
  assert.doesNotMatch(html, /Air-Frame PHY Slices|Amplitude Envelope|输出 IQ 文件路径|生成并导出 IQ 文件/);
  assert.ok(html.indexOf('SDR 实时发射控制') < html.indexOf('原始物理载荷'));
});

test('离线导出显示文件目标与调制命令，操作位于共享参数之前', () => {
  const html = render('export', { transmit: { outputPath: 'outputs/custom.iq' } });
  assert.match(html, /离线 IQ 生成目标配置/);
  assert.match(html, /输出 IQ 文件路径/);
  assert.ok(html.includes('value="outputs/custom.iq"'));
  assert.match(html, /生成并导出 IQ 文件/);
  assert.match(html, /<code>flrc modulate /);
  assert.doesNotMatch(html, /Air-Frame PHY Slices|Amplitude Envelope|立即空口发射|中心发射频率|--frequency|--tx-gain/);
  assert.ok(html.indexOf('生成并导出 IQ 文件') < html.indexOf('原始物理载荷'));
});

test('选项卡状态对应当前面板，往返切换保留载荷、射频参数和文件路径', () => {
  const previous = radio.getState();
  radio.getState().setFrame({ payloadHex: '112233445566', syncWord: 'b42b2bb4', sampleRate: 4000000 });
  radio.getState().setTransmit({ centerFreq: 2440000000, gain: 17, outputPath: 'outputs/kept.iq' });
  const frame = radio.getState().frame;
  const settings = radio.getState().transmit;
  try {
    for (const mode of ['preview', 'live', 'export', 'preview']) {
      radio.getState().setTransmit({ mode });
      const state = radio.getState();
      assert.deepEqual(state.frame, frame);
      assert.deepEqual(state.transmit, { ...settings, mode });
      const html = render(mode, { frame: state.frame, transmit: state.transmit });
      const tabs = html.match(/<button[^>]*role="tab"[^>]*>/g);
      assert.equal(tabs.length, 3);
      assert.equal(tabs.filter(tab => tab.includes('aria-selected="true"')).length, 1);
      assert.match(tabs.find(tab => tab.includes(`id="transmit-tab-${mode}"`)), /aria-selected="true"[^>]*tabindex="0"/);
      assert.match(html, new RegExp(`id="transmit-panel-${mode}" role="tabpanel" aria-labelledby="transmit-tab-${mode}"`));
      assert.match(html, /112233445566/);
      assert.match(html, /value="B42B2BB4"/);
      assert.match(html, /value="4000000"/);
    }
  } finally { radio.setState(previous, true); }
});

test('任务运行时切换三个模式均保留停止入口，离线导出禁止重复启动', () => {
  for (const mode of ['live', 'export', 'preview']) {
    const html = render(mode, { runId: 'active-transmit' });
    const buttons = html.match(/<button[^>]*>.*?<[/]button>/gs);
    const stop = buttons.find(button => /停止发射|停止当前任务/.test(button));
    assert.ok(stop, `${mode} mode must retain the active task stop control`);
    assert.doesNotMatch(stop, /disabled=""/);
    if (mode === 'export') {
      assert.match(buttons.find(button => button.includes('生成并导出 IQ 文件')), /disabled=""/);
    }
  }
});
