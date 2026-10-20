import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import * as echarts from 'echarts/core.js';
import { LineChart } from 'echarts/charts.js';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components.js';
import { SVGRenderer } from 'echarts/renderers.js';

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, SVGRenderer]);
const bundled = await build({ entryPoints: ['src/utils/signalCharts.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { buildSpectrumOption, buildWaveformOption } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('时域图保留文件时间坐标，帧起点由秒正确换算为毫秒', () => {
  const points = [[499.5, 0, 0, 0], [500, .5, .25, .559], [500.5, 0, 0, 0]];
  const option = buildWaveformOption(points, .5);
  assert.deepEqual(option.series[0].data, [[499.5, 0], [500, .5], [500.5, 0]]);
  assert.equal(option.series[2].markLine.data[0].xAxis, 500);
  assert.equal(buildWaveformOption(points).series[2].markLine, undefined);
  const instance = chart();
  try {
    instance.setOption(option, { notMerge: true });
    assert.deepEqual(instance.getModel().getComponent('xAxis').axis.scale.getExtent(), [499.5, 500.5]);
    assert.ok(instance.renderToSVGString().includes('帧起点'));
    instance.setOption(buildWaveformOption(), { notMerge: true });
    assert.equal(instance.getModel().getSeries().length, 0);
  } finally {
    instance.dispose();
  }
});

function chart() {
  return echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 640, height: 368 });
}

test('8192 点频谱使用有限数值刻度并保留原始频率精度', () => {
  const points = Array.from({ length: 8192 }, (_, index) => [2399000000 + index * 976.5625, -70]);
  const option = buildSpectrumOption(points);
  assert.equal(option.series[0].data, points);
  const instance = chart();
  try {
    instance.setOption(option, { notMerge: true });
    const axis = instance.getModel().getComponent('xAxis').axis;
    assert.ok(axis.scale.getTicks().length <= 12);
    assert.deepEqual(axis.scale.getExtent(), [points[0][0], points.at(-1)[0]]);
    assert.ok(instance.renderToSVGString().includes('</svg>'));
    const tooltip = option.tooltip.formatter([{ value: [2403000976.5625, -12.5] }]);
    assert.ok(tooltip.includes('2403.000977 MHz'));
    assert.ok(tooltip.includes('-12.5 dB/Hz'));
  } finally {
    instance.dispose();
  }
});

test('像素抽样保留单个 FFT bin 的窄峰及谷值，包含频带边缘', () => {
  const points = Array.from({ length: 8192 }, (_, index) => [2400000000 + index * 1000, -80]);
  points[1234][1] = -5;
  points[1235][1] = -115;
  const instance = chart();
  try {
    instance.setOption(buildSpectrumOption(points), { notMerge: true });
    const displayed = instance.getModel().getSeriesByIndex(0).getData();
    assert.ok(displayed.count() < points.length / 2);
    const powers = Array.from({ length: displayed.count() }, (_, index) => displayed.get(displayed.mapDimension('y'), index));
    assert.equal(Math.max(...powers), -5);
    assert.equal(Math.min(...powers), -115);
    assert.equal(points.length, 8192);
    points[0][1] = 1;
    points.at(-1)[1] = 2;
    instance.setOption(buildSpectrumOption(points), { notMerge: true });
    const edges = instance.getModel().getSeriesByIndex(0).getData();
    assert.equal(edges.getDataExtent(edges.mapDimension('y'))[1], 2);
  } finally {
    instance.dispose();
  }
});

test('文件失效后的空数据清除已有频谱', () => {
  const instance = chart();
  try {
    instance.setOption(buildSpectrumOption([[1, -80], [2, -30]]), { notMerge: true });
    instance.setOption(buildSpectrumOption(), { notMerge: true });
    assert.equal(instance.getModel().getSeries().length, 0);
  } finally {
    instance.dispose();
  }
});
