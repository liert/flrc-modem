import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const result = await build({
  stdin: { contents: `export { SpectrumView } from './src/views/SpectrumView.tsx';
    export { SpectrumStatusBar } from './src/components/SpectrumStatusBar.tsx';
    export { SpectrumDemodDiagnostics } from './src/components/SpectrumDemodDiagnostics.tsx';
    export { SpectrumFileInput } from './src/components/SpectrumFileInput.tsx';`, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false,
  format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'echarts/*', 'lucide-react'],
  plugins: [{ name: 'check-echarts-core-props', setup(builder) {
    builder.onResolve({ filter: /^echarts-for-react\/lib\/core$/ }, () => ({ path: 'chart', namespace: 'test-chart' }));
    builder.onLoad({ filter: /.*/, namespace: 'test-chart' }, () => ({ loader: 'js', contents: `
      import React from 'react';
      export default function Chart(props) {
        // Core requires an injected engine. SSR of the real wrapper alone never
        // runs componentDidMount, so assert its runtime contract here.
        if (!props.echarts || typeof props.echarts.init !== 'function'
          || typeof props.echarts.getInstanceByDom !== 'function'
          || typeof props.echarts.dispose !== 'function') {
          throw new Error('ECharts core requires the echarts engine');
        }
        return React.createElement('div', { 'data-testid': 'spectrum-chart' });
      }
    ` }));
  } }],
});
// Keep external imports resolving relative to this test rather than a data URL.
const { pathToFileURL } = await import('node:url');
const { join } = await import('node:path');
const { writeFile, unlink } = await import('node:fs/promises');
const target = join(process.cwd(), 'node_modules', `.spectrum-view-test-${process.pid}.mjs`);
let SpectrumView;
let SpectrumStatusBar;
let SpectrumFileInput;
let SpectrumDemodDiagnostics;
try {
  await writeFile(target, result.outputFiles[0].text);
  ({ SpectrumView, SpectrumStatusBar, SpectrumFileInput, SpectrumDemodDiagnostics } = await import(pathToFileURL(target).href));
} finally { await unlink(target); }

test('频谱页面首次进入、设备数据刷新及无采样数据时都传入 ECharts 引擎', () => {
  for (const devices of [[], [{ device_type: 'hackrf', status: 'online', details: { serials: ['test-radio'] } }]]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['devices'], devices);
    try {
      const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(SpectrumView)));
      assert.match(html, /data-testid="spectrum-chart"/);
      assert.match(html, /开始频谱采集/);
      assert.match(html, /<button[^>]*>File<\/button>/);
      assert.doesNotMatch(html, />sim<\/button>|仿真信号/);
      assert.match(html, /实时瀑布图/);
      const tuner = html.match(/<div role="group" aria-label="中心频率"[\s\S]*?<\/div>/)?.[0];
      assert.ok(tuner);
      assert.equal(tuner.match(/role="spinbutton"/g)?.length, 12);
      assert.equal(tuner.match(/class="spec-frequency-dot"/g)?.length, 3);
      assert.doesNotMatch(tuner, /<input|MHz|kHz|Hz/);
      assert.ok(html.indexOf('class="spec-frequency-tuner"') < html.indexOf('aria-label="频谱采集操作"'));
      // Primary actions must remain outside and ahead of the parameter scroller.
      const controls = html.match(/<section aria-label="频谱采集操作"[\s\S]*?<\/section>/)?.[0];
      assert.match(controls, /开始频谱采集/);
      assert.doesNotMatch(html, /应用调节|重启并应用参数/);
      assert.ok(html.indexOf('aria-label="频谱采集操作"') < html.indexOf('id="spectrum-settings"'));
      assert.match(html, /aria-expanded="false" aria-controls="spectrum-settings"/);
      assert.match(html, /<details[^>]*>[\s\S]*?FFT 与刷新/);
      const charts = html.slice(html.indexOf('aria-label="实时频谱与瀑布图"'));
      assert.match(charts, /aria-label="频谱显示操作"[\s\S]*?峰值保持/);
      assert.doesNotMatch(html, /清空瀑布图/);
      assert.equal(charts.match(/grid-template-columns:64px minmax\(0, 1fr\) 56px/g)?.length, 2);
      assert.match(charts, /<canvas[^>]*style="width:100%;left:0%"[^>]*class="absolute/);
    } finally { client.clear(); }
  }
});

test('启动前、采集中、暂停和停止后运行信息保持相同的布局结构', () => {
  const defaults = { sdr: 'usrp', centerFreqMhz: 2480, sampleRate: 8000000, nFft: 1024 };
  const idle = { state: 'idle', device_name: null, device_id: null, actual_center_freq: null,
    actual_sample_rate: null, actual_master_clock: null, rbw_hz: null,
    fps: 0, hw_overflows: 0, client_dropped_frames: 0 };
  const active = { ...idle, state: 'running', device_name: 'USRP B210 with a very long device name',
    device_id: 'serial=12345678901234567890', actual_center_freq: 2480000000,
    actual_sample_rate: 8000000, actual_master_clock: 64000000, rbw_hz: 7812.5, fps: 30 };
  const frame = { peak_freq: 2481000000, peak_power: -102.5, fps: 30, sequence: 123456789 };
  let expected;
  for (const [status, currentFrame] of [[idle, null], [active, null], [active, frame],
    [{ ...active, state: 'paused' }, frame], [{ ...idle, state: 'stopped' }, frame],
    [{ ...idle, state: 'starting' }, null]]) {
    const html = renderToStaticMarkup(React.createElement(SpectrumStatusBar, { ...defaults, status, frame: currentFrame }));
    assert.match(html, /峰值:/);
    assert.match(html, /MCR:/);
    assert.equal(html.match(/spec-metrics-row flex h-6/g)?.length, 1);
    assert.doesNotMatch(html, /设备:|中心:|采样率:/);
    const layout = html.replace(/ title="[^"]*"/g, '').replace(/>[^<]*</g, '><');
    expected ??= layout;
    assert.equal(layout, expected);
  }
});


test('File 输入提供已有文件、本地上传、路径和三种采样格式', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['spectrumFiles'], [{ path: 'outputs/recording.cf32', name: 'recording.cf32', size_bytes: 8192, iq_format: 'cf32' }]);
  try {
    const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(SpectrumFileInput, {
      path: 'outputs/recording.cf32', format: 'cf32', onChange() {}, onUploadingChange() {},
    })));
    assert.match(html, /label for="spectrum-file-select"/);
    assert.match(html, /<option value="outputs\/recording.cf32" selected=""/);
    assert.match(html, /<input[^>]*id="spectrum-file-upload"[^>]*type="file"[^>]*accept=".cs8,.cu8,.cf32,.iq,.bin"/);
    assert.match(html, /label for="spectrum-iq-path"/);
    for (const format of ['cs8', 'cu8', 'cf32']) assert.match(html, new RegExp('option value="' + format + '"'));
    assert.match(html, /循环回放/);
  } finally { client.clear(); }
});

test('实时解调显示处理进度、缺少加速和独立错误，不把零包当成运行成功', () => {
  const status = { demod_windows_count: 12, demod_candidates_count: 3,
    demod_packets_count: 0, demod_dropped_chunks: 7, demod_accelerated: false,
    demod_error_message: 'ValueError: bad IQ' };
  const html = renderToStaticMarkup(React.createElement(SpectrumDemodDiagnostics, { status }))
    .replace(/<!--.*?-->/g, '');
  assert.match(html, /已处理 12 窗/);
  assert.match(html, /候选 3/);
  assert.match(html, /CRC 通过 0 帧/);
  assert.match(html, /丢块 7/);
  assert.match(html, /Numba/);
  assert.match(html, /role="alert"/);
  assert.match(html, /ValueError: bad IQ/);
  const recovered = renderToStaticMarkup(React.createElement(SpectrumDemodDiagnostics, {
    status: { ...status, demod_accelerated: true, demod_error_message: null } }));
  assert.doesNotMatch(recovered, /role="alert"|未启用 Numba/);
});

test('CRC 确认的 BLE 提示与解调错误分开显示', () => {
  const hint = '检测到 CRC 正确的 BLE 1M 广播（信道 39 / 2480MHz）。当前使用 FLRC 解调';
  const html = renderToStaticMarkup(React.createElement(SpectrumDemodDiagnostics, {
    status: { demod_signal_hint: hint, demod_enabled: true, demod_packets_count: 0 } }));
  assert.match(html, /role="status"/);
  assert.match(html, /BLE 1M/);
  assert.doesNotMatch(html, /解调已停止|role="alert"/);
});
