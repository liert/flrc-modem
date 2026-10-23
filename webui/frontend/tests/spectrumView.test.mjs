import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const result = await build({
  stdin: { contents: `export { SpectrumView } from './src/views/SpectrumView.tsx';
    export { SpectrumStatusBar } from './src/components/SpectrumStatusBar.tsx';`, resolveDir: process.cwd(), loader: 'tsx' },
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
try {
  await writeFile(target, result.outputFiles[0].text);
  ({ SpectrumView, SpectrumStatusBar } = await import(pathToFileURL(target).href));
} finally { await unlink(target); }

test('频谱页面首次进入、设备数据刷新及无采样数据时都传入 ECharts 引擎', () => {
  for (const devices of [[], [{ device_type: 'hackrf', status: 'online', details: { serials: ['test-radio'] } }]]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['devices'], devices);
    try {
      const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(SpectrumView)));
      assert.match(html, /data-testid="spectrum-chart"/);
      assert.match(html, /开始频谱采集/);
      assert.match(html, /实时瀑布图/);
      // Primary actions must remain outside and ahead of the parameter scroller.
      const controls = html.match(/<section aria-label="频谱采集操作"[\s\S]*?<\/section>/)?.[0];
      assert.match(controls, /开始频谱采集/);
      assert.match(controls, /<button[^>]*disabled=""[^>]*>[\s\S]*?应用调节/);
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
