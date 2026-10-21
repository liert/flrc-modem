import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const outTarget = join(process.cwd(), 'node_modules', '.cache_spectrum_charts_bundle.mjs');
await build({
  stdin: {
    contents: `
      export {
        buildRealtimeSpectrumOption,
        createTurboColormapLUT,
        renderWaterfallRow,
      } from './src/utils/spectrumCharts.ts';
    `,
    resolveDir: process.cwd(),
    loader: 'ts',
  },
  bundle: true,
  outfile: outTarget,
  format: 'esm',
  platform: 'node',
});

const {
  buildRealtimeSpectrumOption,
  createTurboColormapLUT,
  renderWaterfallRow,
} = await import(pathToFileURL(outTarget).href);

test('buildRealtimeSpectrumOption 生成符合仪器标准的 RF 频率轴与 dBFS 刻度', () => {
  const nFft = 1024;
  const centerFreq = 2480000000;
  const sampleRate = 8000000;
  const rbw = sampleRate / nFft;
  const powers = new Array(nFft).fill(-75.5);
  powers[512] = -12.3; // 峰值在中心偏右

  const option = buildRealtimeSpectrumOption({
    freqMin: centerFreq - sampleRate / 2,
    freqMax: centerFreq + sampleRate / 2 - rbw,
    rbwHz: rbw,
    powers,
    refLevelDb: 0,
    dynamicRangeDb: 80,
    peakFreq: centerFreq,
    peakPower: -12.3,
  });

  // 1. 验证横轴 RF 频率 (MHz)
  assert.equal(option.xAxis.name, 'MHz');
  assert.equal(option.xAxis.min, 2476.0); // 2480 - 4
  assert.equal(option.xAxis.max, (2480000000 + 4000000 - rbw) / 1e6);

  // 2. 验证纵轴单位 dBFS (不能显示为 dBm)
  assert.equal(option.yAxis.name, 'dBFS');
  assert.equal(option.yAxis.max, 0);
  assert.equal(option.yAxis.min, -80);

  // 3. 验证曲线与采样
  assert.equal(option.series[0].name, '当前频谱');
  assert.equal(option.series[0].sampling, 'minmax');
  assert.equal(option.series[0].data.length, nFft);

  // 4. 验证峰值标记
  assert.ok(option.series[0].markPoint);
  assert.equal(option.series[0].markPoint.data[0].coord[1], -12.3);
});

test('buildRealtimeSpectrumOption 启用峰值保持时叠加琥珀色虚线轨迹', () => {
  const nFft = 512;
  const powers = new Array(nFft).fill(-80);
  const peakHold = new Array(nFft).fill(-30);

  const option = buildRealtimeSpectrumOption({
    freqMin: 2400e6,
    freqMax: 2410e6,
    rbwHz: 10e6 / 512,
    powers,
    peakHoldPowers: peakHold,
    refLevelDb: 0,
    dynamicRangeDb: 100,
  });

  assert.equal(option.series.length, 2);
  assert.equal(option.series[1].name, '峰值保持');
  assert.equal(option.series[1].data.length, 512);
  assert.equal(option.series[1].lineStyle.color, '#f59e0b');
  assert.equal(option.series[1].lineStyle.type, 'dashed');
});

test('createTurboColormapLUT 生成 256 色阶连续平滑查找表', () => {
  const lut = createTurboColormapLUT();
  assert.equal(lut.length, 256 * 3);

  // 最低电平接近深色
  const rLow = lut[0];
  const gLow = lut[1];
  const bLow = lut[2];
  assert.ok(rLow < 60);

  // 最高电平为红色/亮黄色
  const rHigh = lut[255 * 3 + 0];
  const bHigh = lut[255 * 3 + 2];
  assert.ok(rHigh > 180);
  assert.ok(bHigh < 100);
});

test('renderWaterfallRow 支持画布平移与断流隔离红线', () => {
  const drawOps = [];
  const fakeCanvas = {
    width: 200,
    height: 100,
    getContext: () => ({
      drawImage: (...args) => drawOps.push({ type: 'drawImage', args }),
      fillRect: (...args) => drawOps.push({ type: 'fillRect', args }),
      createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      putImageData: (...args) => drawOps.push({ type: 'putImageData', args }),
    }),
  };

  // 1. 正常行渲染
  renderWaterfallRow(fakeCanvas, new Array(128).fill(-50), 0, 80, false, 2);
  assert.equal(drawOps[0].type, 'drawImage');
  assert.equal(drawOps[1].type, 'putImageData');

  // 2. 断流隔离红线渲染
  drawOps.length = 0;
  renderWaterfallRow(fakeCanvas, new Array(128).fill(-50), 0, 80, true, 2);
  assert.equal(drawOps[0].type, 'drawImage');
  assert.equal(drawOps[1].type, 'fillRect');
  assert.equal(drawOps[1].args[0], 0);
  assert.equal(drawOps[1].args[3], 2);
});
