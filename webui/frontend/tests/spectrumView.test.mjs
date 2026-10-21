import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const result = await build({
  entryPoints: ['src/views/SpectrumView.tsx'], bundle: true, write: false,
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
try {
  await writeFile(target, result.outputFiles[0].text);
  ({ SpectrumView } = await import(pathToFileURL(target).href));
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
    } finally { client.clear(); }
  }
});
