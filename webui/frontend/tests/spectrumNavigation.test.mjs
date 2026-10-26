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
      export function ${args.path === 'core' ? 'Chart' : args.path}(props) { return React.createElement('div', ${args.path === 'core'
        ? "{ 'data-testid': 'spectrum-chart', onDataZoom: props.onEvents?.datazoom, 'data-freq-min': props.option.xAxis.min, 'data-freq-max': props.option.xAxis.max, 'data-zoom-start': props.option.dataZoom[0].start, 'data-zoom-end': props.option.dataZoom[0].end }"
        : '{}'}); }
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
  const wheelListeners = new Set();
  const sockets = [];
  const starts = [], stops = [], updates = [];
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
  api.updateSpectrum = async (id, request) => { updates.push({ id, request }); return { status }; };
  api.pauseSpectrum = async () => ({ ...status, state: 'paused' });
  api.resumeSpectrum = async () => status;
  useWorkstationStore.setState({ activeTab: 'dashboard', spectrumTargetFreqHz: null });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QueryClientProvider, { client }, React.createElement(App)), {
      createNodeMock(element) {
        if (element.props['aria-label'] === '解调通带位置') return {
          getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
        };
        if (element.props.className === 'spec-frequency-tuner') return {
          contains: () => true,
          addEventListener(type, callback, options) {
            if (type === 'wheel') { assert.equal(options.passive, false); wheelListeners.add(callback); }
          },
          removeEventListener(type, callback) { if (type === 'wheel') wheelListeners.delete(callback); },
        };
        if (element.props.onMouseDown && element.props.onMouseMove && element.props.onMouseUp) {
          return { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }) };
        }
        return null;
      },
    });
  });
  return {
    renderer, starts, stops, updates, sockets, listeners, wheelListeners, status,
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
    await setFrequency(app, 2456000000);
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
    assert.equal(frequencyValue(app), 2456000000);
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

function disabled(node) {
  return Boolean(node.props.disabled || (node.parent && disabled(node.parent)));
}

async function change(app, id, value) {
  await act(async () => app.renderer.root.findByProps({ id }).props.onChange({ target: { value } }));
}

function frequencyValue(app) {
  return Array.from({ length: 12 }, (_, index) => index).reduce((value, index) => value * 10
    + app.renderer.root.findByProps({ id: `spectrum-frequency-${index}` }).props['aria-valuenow'], 0);
}

function clickFrequency(app, index, direction) {
  app.renderer.root.findByProps({ id: `spectrum-frequency-${index}` }).props.onClick({
    clientY: direction > 0 ? 10 : 30,
    currentTarget: { getBoundingClientRect: () => ({ top: 0, height: 40 }) },
  });
}

async function setFrequency(app, value) {
  let current = frequencyValue(app);
  await act(async () => {
    Array.from({ length: 12 }, (_, index) => 10 ** (11 - index)).forEach((step, index) => {
      const direction = Math.sign(value - current);
      const count = Math.floor(Math.abs(value - current) / step);
      for (let i = 0; i < count; i++) clickFrequency(app, index, direction);
      current += direction * count * step;
    });
  });
}

async function settleTuning() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 300)); });
}

for (const ending of ['manual', 'timed', 'capture-stop', 'start']) {
  test(`录制${ending}失败展示原因并清除成功卡片，后续成功录制可恢复`, async () => {
    const app = await mountApp();
    try {
      const message = '登记录制任务或产物失败：injected database failure';
      const failure = { is_recording: false, completed: ending === 'manual', run_id: null,
        filepath: 'outputs/captures/failed.cf32', recorded_bytes: 256, samples_count: 32,
        elapsed_s: .5, error: message };
      const running = { ...failure, is_recording: true, completed: false, error: null };
      api.startSpectrumRecord = async () => ending === 'start' ? failure : running;
      api.stopSpectrumRecord = async () => failure;
      api.getSpectrumRecordStatus = async () => failure;
      await app.navigate('spectrum');
      await app.click('开始频谱采集');
      const startRecord = () => app.renderer.root.findAllByType('button')
        .find(node => node.props.className?.includes('spec-record-start'));
      await act(async () => startRecord().props.onClick());
      if (ending === 'manual') {
        await act(async () => app.renderer.root.findAllByType('button').find(node => text(node).includes('● REC')).props.onClick());
      } else if (ending === 'capture-stop') {
        await app.click('停止采集');
      } else if (ending === 'timed') {
        await settleTuning();
      }
      assert.match(text(app.renderer.root), /登记录制任务或产物失败：injected database failure/);
      assert.doesNotMatch(text(app.renderer.root), /IQ 录制完成/);
      assert.ok(startRecord(), 'Failed recordings must leave recording mode');
      assert.equal(useWorkstationStore.getState().activeTab, 'spectrum');

      if (ending === 'capture-stop') await app.click('开始频谱采集');
      api.startSpectrumRecord = async () => ({ ...failure, completed: true, error: null, run_id: 'recorded-1' });
      await act(async () => startRecord().props.onClick());
      assert.match(text(app.renderer.root), /IQ 录制完成/);
      assert.doesNotMatch(text(app.renderer.root), /injected database failure/);
    } finally { await app.dispose(); }
  });
}

test('停止采集显示录制失败后，迟到的录制中轮询不会恢复 REC 状态', async () => {
  const app = await mountApp();
  let resolvePoll;
  try {
    const running = { is_recording: true, completed: false, elapsed_s: .1,
      recorded_bytes: 256, samples_count: 32, error: null };
    api.startSpectrumRecord = async () => running;
    let polls = 0;
    api.getSpectrumRecordStatus = () => ++polls === 1
      ? new Promise(resolve => { resolvePoll = resolve; })
      : Promise.resolve({ ...running, is_recording: false, error: '刷新 IQ 文件失败：disk full' });
    await app.navigate('spectrum');
    await app.click('开始频谱采集');
    await act(async () => app.renderer.root.findAllByType('button')
      .find(node => node.props.className?.includes('spec-record-start')).props.onClick());
    await settleTuning();
    assert.ok(resolvePoll);
    await app.click('停止采集');
    await act(async () => resolvePoll(running));
    assert.match(text(app.renderer.root), /刷新 IQ 文件失败：disk full/);
    assert.doesNotMatch(text(app.renderer.root), /● REC|IQ 录制完成/);
  } finally { await app.dispose(); }
});

function demodSlider(app) {
  return app.renderer.root.findByProps({ 'aria-label': '解调中心频率' });
}

async function moveDemod(app, key, options = {}) {
  await act(async () => demodSlider(app).props.onKeyDown({ key, ...options, preventDefault() {}, stopPropagation() {} }));
}

function assertDemodVisible(app) {
  const { left, width } = demodSlider(app).props.style;
  assert.ok(parseFloat(left) >= -1e-7, `left=${left}`);
  assert.ok(parseFloat(left) + parseFloat(width) <= 100 + 1e-7, `left=${left}, width=${width}`);
}

test('解调框保持相对频偏，中心和解调频率同请求更新，迟到频谱帧不改变频偏', async () => {
  const app = await mountApp();
  try {
    await app.navigate('spectrum');
    await setFrequency(app, 2456000000);
    await moveDemod(app, 'ArrowRight', { shiftKey: true });
    assert.equal(demodSlider(app).props['aria-valuenow'], 2456010000);
    await app.click('开启解调');
    await app.click('开始频谱采集');
    assert.equal(app.starts[0].demod_center_freq - app.starts[0].center_freq, 10000);
    const stream = app.sockets.find(socket => socket.url.includes('/ws/spectrum'));
    const frame = (center, sequence) => ({ sequence, timestamp: 100 + sequence,
      center_freq: center, sample_rate: 8000000, n_fft: 4, freq_min: center - 4000000,
      freq_max: center + 3999000, rbw_hz: 1000, powers: [-80, -20, -40, -80], fps: 15,
      peak_freq: center, peak_power: -20, hw_overflows: 0, dsp_dropped_frames: 0, client_dropped_frames: 0 });
    await act(async () => stream.receive(frame(2456000000, 1)));
    await moveDemod(app, 'End');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2459648999, 'Respect the actual last FFT bin, not a theoretical symmetric span');
    assertDemodVisible(app);
    await app.click('回中');
    await moveDemod(app, 'ArrowRight', { shiftKey: true });
    await setFrequency(app, 2480000000);
    assert.equal(demodSlider(app).props['aria-valuenow'], 2456010000, 'Pending retune keeps the band inside the old displayed frame');
    assertDemodVisible(app);
    await settleTuning();
    assert.deepEqual(app.updates.at(-1).request, { center_freq: 2480000000, demod_center_freq: 2480010000 });
    await act(async () => stream.receive(frame(2456000000, 2)));
    await settleTuning();
    assert.equal(app.updates.length, 1, 'An old frame must not retune the receiver backwards');
    await act(async () => stream.receive(frame(2480000000, 3)));
    assert.equal(demodSlider(app).props['aria-valuenow'], 2480010000);
    assertDemodVisible(app);
    await app.click('暂停显示');
    await moveDemod(app, 'ArrowLeft', { shiftKey: true });
    await settleTuning();
    assert.deepEqual(app.updates.at(-1).request, { center_freq: 2480000000, demod_center_freq: 2480000000 });
    assert.ok(app.renderer.root.findAllByType('button').some(node => text(node) === '恢复显示'));
  } finally { await app.dispose(); }
});

test('解调拖动与中心调谐共用串行队列，连续操作保留最新频偏且停止后忽略旧响应', async () => {
  const app = await mountApp();
  let resolveUpdate;
  try {
    await app.navigate('spectrum');
    await app.click('开始频谱采集');
    const updates = [];
    api.updateSpectrum = (_id, request) => { updates.push(request); return new Promise(resolve => { resolveUpdate = resolve; }); };
    await moveDemod(app, 'ArrowRight', { shiftKey: true });
    await settleTuning();
    await setFrequency(app, 2450000000);
    await moveDemod(app, 'ArrowRight', { shiftKey: true });
    await settleTuning();
    assert.deepEqual(updates, [{ center_freq: 2480000000, demod_center_freq: 2480010000 }]);
    await act(async () => resolveUpdate({ status: app.status }));
    assert.deepEqual(updates.at(-1), { center_freq: 2450000000, demod_center_freq: 2450020000 });
    assert.equal(demodSlider(app).props['aria-valuenow'], 2450020000);
    await app.click('停止采集');
    await act(async () => resolveUpdate({ status: app.status }));
    assert.ok(app.renderer.root.findAllByType('button').some(node => text(node) === '开始频谱采集'));
  } finally { await app.dispose(); }
});

test('采样率缩小时完整解调通带自动限位，扩大后保留频偏，再次启动使用调整后的频率', async () => {
  const app = await mountApp();
  try {
    await app.navigate('spectrum');
    await moveDemod(app, 'End');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2483649999);
    await change(app, 'spectrum-sample-rate', '2000000');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2480649999);
    assertDemodVisible(app);
    await change(app, 'spectrum-sample-rate', '8000000');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2480649999, 'Keep the clamped offset instead of jumping back');
    await setFrequency(app, 2456000000);
    assert.equal(demodSlider(app).props['aria-valuenow'], 2456649999);
    await app.click('开始频谱采集');
    assert.equal(app.starts[0].demod_center_freq, 2456649999);
    await app.click('停止采集');
    await change(app, 'spectrum-sample-rate', '2000000');
    assertDemodVisible(app);
    await app.click('开始频谱采集');
    assert.equal(app.starts.at(-1).demod_center_freq, 2456649999);
    assert.equal(app.starts.at(-1).sample_rate, 2000000);
  } finally { await app.dispose(); }
});

test('缩放和拖动限制完整解调框，视窗过窄时自动放宽且回中恢复全频带', async () => {
  const app = await mountApp();
  try {
    await app.navigate('spectrum');
    const chart = () => app.renderer.root.findByProps({ 'data-testid': 'spectrum-chart' });
    await act(async () => chart().props.onDataZoom({ start: 60, end: 80 }));
    assert.equal(demodSlider(app).props['aria-valuenow'], 2481150001);
    assertDemodVisible(app);
    await moveDemod(app, 'End');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2482049999);
    assertDemodVisible(app);
    const captures = new Set();
    const target = { focus() {}, setPointerCapture(id) { captures.add(id); },
      hasPointerCapture: id => captures.has(id), releasePointerCapture(id) { captures.delete(id); } };
    await act(async () => demodSlider(app).props.onPointerDown({ button: 0, pointerId: 1, clientX: 90,
      currentTarget: target, preventDefault() {}, stopPropagation() {} }));
    await act(async () => demodSlider(app).props.onPointerUp({ pointerId: 1, clientX: -1000, stopPropagation() {} }));
    assert.equal(demodSlider(app).props['aria-valuenow'], 2481150001);
    assertDemodVisible(app);
    await act(async () => chart().props.onDataZoom({ batch: [{ start: 50, end: 51 }] }));
    const { 'data-freq-min': min, 'data-freq-max': max, 'data-zoom-start': start, 'data-zoom-end': end } = chart().props;
    assert.ok((max - min) * (end - start) / 100 >= .700002);
    assertDemodVisible(app);
    await app.click('回中');
    assert.equal(demodSlider(app).props['aria-valuenow'], 2480000000);
    assert.equal(chart().props['data-zoom-start'], 0);
    assert.equal(chart().props['data-zoom-end'], 100);
  } finally { await app.dispose(); }
});

test('采样带宽小于解调通带时显示禁用框和原因，恢复采样率后自动可用', async () => {
  const app = await mountApp();
  try {
    api.getSpectrumFiles = async () => [];
    await app.navigate('spectrum');
    await app.click('File');
    await change(app, 'spectrum-sample-rate', '500000');
    assert.equal(demodSlider(app).props['aria-disabled'], true);
    assert.match(text(app.renderer.root), /采样带宽不足，请提高采样率/);
    assertDemodVisible(app);
    const frequency = demodSlider(app).props['aria-valuenow'];
    await moveDemod(app, 'End');
    assert.equal(demodSlider(app).props['aria-valuenow'], frequency);
    await change(app, 'spectrum-sample-rate', '2000000');
    assert.equal(demodSlider(app).props['aria-disabled'], false);
    assertDemodVisible(app);
  } finally { await app.dispose(); }
});

test('每位频率独立成格，支持按位点击、滚轮和进退位，采集中自动调谐', async () => {
  const app = await mountApp();
  try {
    await app.navigate('spectrum');
    const tuner = () => app.renderer.root.findByProps({ className: 'spec-frequency-tuner' });
    assert.equal(text(tuner()), '002.480.000.000');
    assert.equal(tuner().findAllByType('input').length, 0);
    const digits = tuner().findAllByType('button');
    assert.equal(digits.length, 12);
    assert.ok(digits.every(digit => /^\d$/.test(text(digit))));
    assert.equal(app.wheelListeners.size, 1);
    for (let index = 0; index < 12; index++) {
      await act(async () => clickFrequency(app, index, 1));
      assert.equal(frequencyValue(app), 2480000000 + 10 ** (11 - index));
      await act(async () => clickFrequency(app, index, -1));
      assert.equal(frequencyValue(app), 2480000000);
    }
    await act(async () => clickFrequency(app, 5, 1));
    assert.equal(text(tuner()), '002.481.000.000');
    await act(async () => clickFrequency(app, 5, -1));
    assert.equal(text(tuner()), '002.480.000.000');
    await act(async () => clickFrequency(app, 11, -1));
    assert.equal(text(tuner()), '002.479.999.999');
    let prevented = 0;
    const wheel = (index, deltaY, ctrlKey = false) => {
      const step = 10 ** (11 - index);
      app.wheelListeners.forEach(listener => listener({ deltaY, ctrlKey,
        target: { closest: () => ({ dataset: { frequencyStep: String(step) } }) },
        preventDefault() { prevented++; }, stopPropagation() {},
      }));
    };
    await act(async () => { wheel(11, -100); wheel(11, -100); });
    assert.equal(text(tuner()), '002.480.000.001', 'Fast wheel events must both apply');
    assert.equal(prevented, 2);
    await act(async () => wheel(5, 100));
    assert.equal(text(tuner()), '002.479.000.001');
    await act(async () => wheel(5, -100, true));
    assert.equal(text(tuner()), '002.479.000.001', 'Do not intercept pinch zoom');
    await app.click('开始频谱采集');
    assert.equal(app.starts[0].center_freq, 2479000001);
    await act(async () => wheel(8, -100));
    await settleTuning();
    assert.deepEqual(app.updates.at(-1).request, { center_freq: 2479001001, demod_center_freq: 2479001001 });
    await app.click('停止采集');
    await setFrequency(app, 1);
    await act(async () => { clickFrequency(app, 11, -1); wheel(0, 100); });
    assert.equal(frequencyValue(app), 1, 'Keep the frequency positive');
  } finally { await app.dispose(); }
  assert.equal(app.wheelListeners.size, 0, 'Unmount removes the native wheel listener');
});

test('采集和暂停期间锁定静态配置，停止后解锁；可调参数自动合并生效', async () => {
  const app = await mountApp();
  try {
    await app.navigate('spectrum');
    const input = id => app.renderer.root.findByProps({ id });
    assert.equal(disabled(input('spectrum-sample-rate')), false);
    await app.click('开始频谱采集');
    assert.ok(!app.renderer.root.findAllByType('button').some(node => /应用调节|重启并应用参数/.test(text(node))));
    for (const id of ['command-hardware-device', 'spectrum-sample-rate', 'spectrum-n-fft', 'spectrum-window']) {
      assert.equal(disabled(input(id)), true, id);
    }
    for (const label of ['HACKRF', 'USRP', 'File']) {
      assert.equal(disabled(app.renderer.root.findAllByType('button').find(node => text(node) === label)), true);
    }
    const bias = app.renderer.root.findAllByType('input').find(node => node.props.type === 'checkbox' && text(node.parent).includes('Bias-Tee'));
    assert.equal(disabled(bias), true);
    for (const id of ['spectrum-frequency-0', 'spectrum-lna-gain', 'spectrum-vga-gain', 'spectrum-average', 'spectrum-fps-limit', 'spectrum-ref-level', 'spectrum-dynamic-range']) {
      assert.equal(disabled(input(id)), false, id);
    }
    await setFrequency(app, 2450000000);
    await setFrequency(app, 2451000000);
    await change(app, 'spectrum-lna-gain', '32');
    await change(app, 'spectrum-average', '8');
    await change(app, 'spectrum-fps-limit', '20');
    await change(app, 'spectrum-ref-level', '-10');
    await change(app, 'spectrum-dynamic-range', '80');
    await settleTuning();
    assert.deepEqual(app.updates, [{ id: 'capture-1', request: {
      center_freq: 2451000000, demod_center_freq: 2451000000, lna_gain: 32, fft_average: 8, fps_limit: 20,
      ref_level_dbfs: -10, dynamic_range_db: 80,
    } }]);
    await app.click('暂停显示');
    assert.equal(disabled(input('spectrum-n-fft')), true);
    await change(app, 'spectrum-vga-gain', '30');
    await settleTuning();
    assert.deepEqual(app.updates.at(-1).request, { vga_gain: 30 });
    assert.ok(app.renderer.root.findAllByType('button').some(node => text(node) === '恢复显示'));
    await setFrequency(app, 2452000000);
    await app.click('停止采集');
    await settleTuning();
    assert.equal(app.updates.length, 2, 'Stop cancels unsent changes');
    assert.equal(disabled(input('spectrum-sample-rate')), false);
    assert.equal(disabled(input('command-hardware-device')), false);
    assert.equal(disabled(bias), false);
    await change(app, 'spectrum-average', '16');
    await settleTuning();
    assert.equal(app.updates.length, 2, 'Idle edits are kept for the next start');
  } finally { await app.dispose(); }
});

test('启动期间锁定配置，USRP 和文件采集锁定各自的静态参数', async () => {
  for (const source of ['USRP', 'File']) {
    const app = await mountApp();
    try {
      await app.navigate('spectrum');
      await app.click(source);
      if (source === 'File') await change(app, 'spectrum-iq-path', 'outputs/test.cs8');
      let resolveStart;
      api.startSpectrum = () => new Promise(resolve => { resolveStart = resolve; });
      await act(async () => { void app.renderer.root.findAllByType('button').find(node => text(node) === (source === 'File' ? '开始文件回放' : '开始频谱采集')).props.onClick(); });
      assert.equal(disabled(app.renderer.root.findByProps({ id: 'spectrum-frequency-0' })), true);
      await act(async () => resolveStart({ ...app.status, device_type: source === 'File' ? 'file' : 'usrp' }));
      const locked = source === 'File'
        ? ['spectrum-file-select', 'spectrum-file-upload', 'spectrum-iq-path', 'spectrum-iq-format', 'spectrum-sample-rate']
        : ['command-hardware-device', 'spectrum-usrp-mcr', 'spectrum-usrp-antenna', 'spectrum-usrp-channel', 'spectrum-sample-rate'];
      for (const id of locked) assert.equal(disabled(app.renderer.root.findByProps({ id })), true, id);
      if (source === 'USRP') {
        await change(app, 'spectrum-usrp-gain', '42');
        await settleTuning();
        assert.deepEqual(app.updates.at(-1).request, { gain: 42 });
      }
    } finally { await app.dispose(); }
  }
});

test('自动调节串行发送最新参数，失败显示错误，停止后忽略迟到响应', async () => {
  const app = await mountApp();
  let resolveUpdate;
  try {
    await app.navigate('spectrum');
    await app.click('开始频谱采集');
    const updates = [];
    api.updateSpectrum = (id, request) => { updates.push({ id, request }); return new Promise(resolve => { resolveUpdate = resolve; }); };
    await change(app, 'spectrum-lna-gain', '32');
    await settleTuning();
    await change(app, 'spectrum-vga-gain', '30');
    await settleTuning();
    await change(app, 'spectrum-vga-gain', '32');
    await settleTuning();
    assert.equal(updates.length, 1);
    await act(async () => resolveUpdate({ status: app.status }));
    assert.deepEqual(updates.map(update => update.request), [{ lna_gain: 32 }, { vga_gain: 32 }]);
    await act(async () => resolveUpdate({ status: app.status }));
    api.updateSpectrum = async () => { throw new Error('device busy'); };
    await change(app, 'spectrum-average', '8');
    await settleTuning();
    assert.match(text(app.renderer.root), /自动调节失败：device busy/);
    api.updateSpectrum = (_id, request) => { updates.push({ request }); return new Promise(resolve => { resolveUpdate = resolve; }); };
    await change(app, 'spectrum-average', '16');
    await settleTuning();
    assert.deepEqual(updates.at(-1).request, { fft_average: 16 });
    await app.click('停止采集');
    await act(async () => resolveUpdate({ status: app.status }));
    assert.ok(app.renderer.root.findAllByType('button').some(node => text(node) === '开始频谱采集'));
  } finally { await app.dispose(); }
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
