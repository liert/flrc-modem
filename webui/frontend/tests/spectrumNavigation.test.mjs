import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// Exercise App's real navigation and SpectrumView's real effects and stream.
// Only replace unrelated pages, shell chrome and the browser chart renderer.
const result = await build({
  stdin: { contents: `
    export { App } from './src/App.tsx';
    export { api } from './src/api/client.ts';
    export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
  `, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'zustand', 'echarts/*', 'lucide-react'],
  plugins: [{ name: 'browser-and-unrelated-views', setup(builder) {
    builder.onResolve({ filter: /(?:views\/[^/]+|components\/layout\/[^/]+|components\/DeviceDiagnosticsDrawer|echarts-for-react\/lib\/core)$/ }, args => {
      if (args.path.endsWith('/SpectrumView')) return;
      return { path: args.path.split('/').at(-1), namespace: 'test-view' };
    });
    builder.onLoad({ filter: /.*/, namespace: 'test-view' }, args => ({ loader: 'js', contents: `
      import React from 'react';
      export function ${args.path === 'core' ? 'Chart' : args.path}() { return React.createElement('div'); }
      export default ${args.path === 'core' ? 'Chart' : args.path};
    ` }));
  } }],
});
const target = join(process.cwd(), 'node_modules', `.spectrum-navigation-${process.pid}.mjs`);
let modules;
try {
  await writeFile(target, result.outputFiles[0].text);
  modules = await import(pathToFileURL(target).href);
} finally { await unlink(target); }
const { App, api, useWorkstationStore } = modules;

function text(node) {
  return typeof node === 'string' ? node : (node.children || []).map(text).join('');
}

async function mountApp() {
  const previousWindow = globalThis.window;
  const previousSocket = globalThis.WebSocket;
  const previousState = useWorkstationStore.getState();
  const previousApi = { ...api };
  const listeners = new Set();
  const sockets = [];
  const starts = [], stops = [];
  const status = { session_id: 'capture-1', state: 'running', device_type: 'hackrf',
    actual_center_freq: 2456000000, actual_sample_rate: 8000000, n_fft: 1024,
    fps: 15, hw_overflows: 0, dsp_dropped_frames: 0, client_dropped_frames: 0, demod_enabled: true };
  globalThis.window = {
    location: { protocol: 'http:', host: 'localhost' },
    setTimeout, clearTimeout, setInterval, clearInterval,
    addEventListener(type, callback) { if (type === 'keydown') listeners.add(callback); },
    removeEventListener(type, callback) { if (type === 'keydown') listeners.delete(callback); },
  };
  globalThis.WebSocket = class {
    readyState = 1;
    closed = false;
    constructor(url) { this.url = url; sockets.push(this); }
    send() {}
    close() { this.closed = true; this.onclose?.({ code: 1000 }); }
    receive(data) { this.onmessage?.({ data: JSON.stringify({ session_id: status.session_id, ...data }) }); }
  };
  api.getDevices = async () => [];
  api.getTemplates = async () => [];
  api.startSpectrum = async request => { starts.push(request); return status; };
  api.stopSpectrum = async id => { stops.push(id); return { ...status, state: 'stopped' }; };
  useWorkstationStore.setState({ activeTab: 'dashboard', spectrumTargetFreqHz: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(App)), {
      createNodeMock(element) {
        if (element.props.onMouseDown && element.props.onMouseMove && element.props.onMouseUp) {
          return { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) };
        }
        return null;
      },
    });
  });
  return {
    renderer, starts, stops, sockets, listeners, status,
    async navigate(tab) { await act(async () => useWorkstationStore.getState().setActiveTab(tab)); },
    async click(label) {
      const button = renderer.root.findAllByType('button').find(node => text(node) === label);
      assert.ok(button, `Missing button: ${label}`);
      await act(async () => button.props.onClick());
    },
    async dispose() {
      await act(async () => renderer.unmount());
      client.clear();
      useWorkstationStore.setState(previousState, true);
      Object.assign(api, previousApi);
      globalThis.window = previousWindow;
      globalThis.WebSocket = previousSocket;
    },
  };
}

test('往返频谱页面保留会话、配置、频谱帧和解调历史，隐藏时继续收包且不处理快捷键', async () => {
  const app = await mountApp();
  try {
    const spectrum = () => app.renderer.root.findAllByProps({ className: 'spec-plots min-h-0' });
    assert.equal(spectrum().length, 0, 'Never opened spectrum must stay lazy');
    await app.navigate('spectrum');
    const plot = spectrum()[0];
    await act(async () => app.renderer.root.findByProps({ id: 'spectrum-center-freq' }).props.onChange({ target: { value: '2456000000' } }));
    await app.click('开启解调');
    await app.click('开始频谱采集');
    assert.equal(app.starts.length, 1);
    const stream = app.sockets.find(socket => socket.url.includes('/ws/spectrum'));
    assert.ok(stream);
    const receivePacket = async hex => act(async () => {
      stream.receive({ type: 'demod_packet', packet: { time_s: 1, payload_hex: hex, crc_ok: true } });
      await new Promise(resolve => setTimeout(resolve, 120));
    });
    const receiveFrame = sequence => stream.receive({ sequence, timestamp: 100 + sequence / 10,
      center_freq: 2456000000, sample_rate: 8000000, n_fft: 4, freq_min: 2452000000,
      freq_max: 2460000000, powers: [-80, -20, -40, -80], peak_freq: 2456000000,
      peak_power: -20, rbw_hz: 2000000, fps: 15, hw_overflows: 0,
      dsp_dropped_frames: 0, client_dropped_frames: 0 });
    await act(async () => receiveFrame(1));
    await receivePacket('aabb');
    await act(async () => app.renderer.root.findByProps({ 'aria-label': '过滤实时解调数据包' }).props.onChange({ target: { value: 'aa' } }));
    assert.equal(app.listeners.size, 1);

    await app.navigate('dashboard');
    assert.equal(spectrum()[0], plot, 'The original chart subtree must remain mounted');
    assert.equal(app.renderer.root.findByProps({ hidden: true }).props.hidden, true);
    assert.equal(app.stops.length, 0);
    assert.equal(stream.closed, false);
    assert.equal(app.listeners.size, 0, 'Hidden spectrum must not consume other pages\' keys');
    await act(async () => receiveFrame(2));
    await receivePacket('aacc');
    await app.navigate('spectrum');

    assert.equal(spectrum()[0], plot);
    assert.equal(app.starts.length, 1, 'Returning must not restart acquisition');
    assert.equal(app.stops.length, 0);
    assert.equal(app.sockets.filter(socket => socket.url.includes('/ws/spectrum')).length, 1);
    assert.equal(app.renderer.root.findByProps({ id: 'spectrum-center-freq' }).props.value, 2456000000);
    assert.equal(app.renderer.root.findByProps({ 'aria-label': '过滤实时解调数据包' }).props.value, 'aa');
    assert.equal(app.renderer.root.findByProps({ 'aria-label': '实时解调数据流' }).props['data-open'], true);
    assert.equal(app.renderer.root.findByType('tbody').findAllByType('tr').length, 2);
    assert.match(text(app.renderer.root.findByProps({ 'aria-label': '频谱运行信息' })), /频谱帧:2/);
    assert.equal(app.listeners.size, 1);

    await app.click('停止采集');
    await app.navigate('dashboard');
    await app.navigate('spectrum');
    assert.equal(app.renderer.root.findByType('tbody').findAllByType('tr').length, 2, 'Stopped sessions retain their packet history');
    assert.equal(app.stops.length, 1, 'Only the explicit stop action stops acquisition');
  } finally { await app.dispose(); }
});

test('启动期间离开频谱页面，迟到的启动响应仍建立原会话，应用卸载时才释放', async () => {
  const app = await mountApp();
  let resolveStart;
  try {
    api.startSpectrum = () => new Promise(resolve => { resolveStart = resolve; });
    await app.navigate('spectrum');
    await act(async () => {
      void app.renderer.root.findAllByType('button').find(node => text(node) === '开始频谱采集').props.onClick();
    });
    await app.navigate('dashboard');
    await act(async () => resolveStart(app.status));
    assert.equal(app.stops.length, 0);
    assert.equal(app.sockets.filter(socket => socket.url.includes('/ws/spectrum')).length, 1);
    await app.navigate('spectrum');
    assert.ok(app.renderer.root.findAllByType('button').some(node => text(node) === '停止采集'));
  } finally { await app.dispose(); }
  assert.deepEqual(app.stops, ['capture-1']);
});

async function selectWaterfall(app, { physicalTemplate, protocol } = {}) {
  await app.navigate('spectrum');
  if (physicalTemplate) await act(async () => app.renderer.root.findByProps({ id: 'spectrum-demod-template' }).props.onChange({ target: { value: physicalTemplate } }));
  if (protocol) await act(async () => app.renderer.root.findByProps({ id: 'spectrum-demod-proto' }).props.onChange({ target: { value: protocol } }));
  await app.click('开始频谱采集');
  const stream = app.sockets.find(socket => socket.url.includes('/ws/spectrum'));
  await act(async () => stream.receive({ sequence: 1, timestamp: 120,
    center_freq: 2456000000, sample_rate: 7680000, n_fft: 4, freq_min: 2452160000,
    freq_max: 2459840000, powers: [-80, -20, -40, -80], peak_freq: 2456000000,
    peak_power: -20, rbw_hz: 1920000, fps: 15, hw_overflows: 0,
    dsp_dropped_frames: 0, client_dropped_frames: 0,
    iq_history: { time_start: 100, time_end: 120, bytes: 1000, max_bytes: 268435456, max_seconds: 20 },
  }));
  const waterfall = () => app.renderer.root.findAllByType('div').find(node => node.props.onMouseDown && node.props.onMouseUp);
  await act(async () => waterfall().props.onMouseDown({ button: 0, clientX: 55, clientY: 10 }));
  await act(async () => waterfall().props.onMouseMove({ clientX: 75, clientY: 15 }));
  await act(async () => waterfall().props.onMouseUp());
  return { time_start: 117, time_end: 118, sample_rate: 7680000,
    center_frequency: 2456000000, target_frequency: 2457152000 };
}

test('框选后先提取真实 IQ，成功才跳转，草稿使用实际采集参数和目标频点', async () => {
  const app = await mountApp();
  let resolveExport;
  const exports = [];
  try {
    const expected = await selectWaterfall(app);
    const oldDraft = { formData: { iq: 'old.cs8', center_frequency: 1, iq_format: 'cs8', max_seconds: .1, parser: 'old:parser' }, taskName: 'old', manualOutputs: {} };
    useWorkstationStore.getState().saveCommandDraft('demod', oldDraft);
    api.exportSpectrumSelection = (id, req) => {
      exports.push({ id, req });
      return new Promise(resolve => { resolveExport = resolve; });
    };
    let clickPromise;
    await act(async () => {
      const button = app.renderer.root.findAllByType('button').find(node => text(node) === '送入解调');
      clickPromise = button.props.onClick();
      void button.props.onClick(); // Double clicks cannot export twice.
    });
    assert.deepEqual(exports, [{ id: 'capture-1', req: expected }]);
    assert.equal(useWorkstationStore.getState().activeTab, 'spectrum');
    assert.equal(useWorkstationStore.getState().commandDrafts.demod, oldDraft);
    assert.ok(app.renderer.root.findAllByType('button').filter(node => text(node) === '正在提取 IQ…').every(node => node.props.disabled));
    await act(async () => {
      resolveExport({ ...expected, session_id: 'capture-1', filepath: 'outputs/captures/selection.cf32',
        iq_format: 'cf32', samples_count: 7680000, duration_s: 1, bytes: 61440000 });
      await clickPromise;
    });
    const state = useWorkstationStore.getState();
    assert.equal(state.activeTab, 'commands');
    assert.equal(state.selectedCommandId, 'demod');
    const draft = state.commandDrafts.demod.formData;
    assert.equal(draft.iq, 'outputs/captures/selection.cf32');
    assert.equal(draft.iq_format, 'cf32');
    assert.equal(draft.sample_rate, 7680000, 'Do not reuse the uncoerced 8 MHz UI input');
    assert.equal(draft.center_frequency, 2456000000);
    assert.equal(draft.frequency, 2457152000);
    assert.equal(draft.max_seconds, 0);
    assert.equal(draft.parser, '');
    assert.equal(draft.config, '');
    assert.equal(app.stops.length, 0);
  } finally { await app.dispose(); }
});

test('已选时段随瀑布图滚动，导出期间离开后不强制跳回命令页', async () => {
  const app = await mountApp();
  let resolveExport;
  try {
    const expected = await selectWaterfall(app);
    const stream = app.sockets.find(socket => socket.url.includes('/ws/spectrum'));
    await act(async () => stream.receive({ sequence: 2, timestamp: 122,
      center_freq: 2456000000, sample_rate: 7680000, n_fft: 4, freq_min: 2452160000,
      freq_max: 2459840000, powers: [-80, -20, -40, -80], peak_freq: 2456000000,
      peak_power: -20, rbw_hz: 1920000, fps: 15, hw_overflows: 0,
      dsp_dropped_frames: 0, client_dropped_frames: 0 }));
    const overlay = app.renderer.root.findAllByType('div').find(node => node.props.className?.includes('border-2 border-cyan-400 bg-cyan-400/10'));
    assert.equal(overlay.props.style.top, '20%');
    api.exportSpectrumSelection = (_id, req) => {
      assert.deepEqual(req, expected, 'Incoming frames must not change the selected time window');
      return new Promise(resolve => { resolveExport = resolve; });
    };
    let pending;
    await act(async () => { pending = app.renderer.root.findAllByType('button').find(node => text(node) === '送入解调').props.onClick(); });
    await app.navigate('dashboard');
    await act(async () => {
      resolveExport({ ...expected, session_id: 'capture-1', filepath: 'outputs/captures/pinned.cs8',
        iq_format: 'cs8', samples_count: 7680000, duration_s: 1, bytes: 15360000 });
      await pending;
    });
    assert.equal(useWorkstationStore.getState().activeTab, 'dashboard');
    assert.equal(useWorkstationStore.getState().commandDrafts.demod.formData.iq, 'outputs/captures/pinned.cs8');
  } finally { await app.dispose(); }
});

test('选区已过期或导出失败时留在频谱页并显示原因，不覆盖原解调草稿', async () => {
  const app = await mountApp();
  try {
    await selectWaterfall(app);
    const oldDraft = useWorkstationStore.getState().commandDrafts.demod;
    api.exportSpectrumSelection = async () => { throw new Error('选区原始 IQ 已过期，请重新框选'); };
    await app.click('送入解调');
    assert.equal(useWorkstationStore.getState().activeTab, 'spectrum');
    assert.equal(useWorkstationStore.getState().commandDrafts.demod, oldDraft);
    assert.match(text(app.renderer.root.findByProps({ role: 'alert' })), /IQ 已过期/);
    assert.ok(app.renderer.root.findAllByType('button').filter(node => text(node) === '送入解调').every(node => !node.props.disabled));
  } finally { await app.dispose(); }
});

for (const [physicalTemplate, protocol, expectedConfig] of [
  ['physical/dk32s.toml', 'dk32s', 'templates/physical/dk32s.toml'],
  [undefined, 'h12', 'templates/physical/h12.toml'],
]) {
  test(`选区解调继承当前物理配置 ${expectedConfig}，不沿用旧草稿的物理覆盖`, async () => {
    const app = await mountApp();
    try {
      const selection = await selectWaterfall(app, { physicalTemplate, protocol });
      useWorkstationStore.getState().saveCommandDraft('demod', {
        formData: { bt: .5, crc_bytes: 2, header_type: 'variable', config: 'old.toml' },
        taskName: 'old', manualOutputs: {},
      });
      api.exportSpectrumSelection = async () => ({ ...selection, session_id: 'capture-1',
        filepath: 'outputs/captures/selection.cs8', iq_format: 'cs8', samples_count: 7680000,
        duration_s: 1, bytes: 15360000 });
      await app.click('送入解调');
      const draft = useWorkstationStore.getState().commandDrafts.demod.formData;
      assert.equal(draft.config, expectedConfig);
      assert.equal(draft.protocol, protocol === 'dk32s' ? 'dk32s' : '');
      assert.equal(draft.bt, undefined);
      assert.equal(draft.crc_bytes, undefined);
      assert.equal(draft.header_type, undefined);
    } finally { await app.dispose(); }
  });
}
