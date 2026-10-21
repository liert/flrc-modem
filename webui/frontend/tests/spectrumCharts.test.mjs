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
        findNearestHistoryFrame,
        calculateWaterfallSelection,
        analyzeWindowFrames,
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
  findNearestHistoryFrame,
  calculateWaterfallSelection,
  analyzeWindowFrames,
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

test('buildRealtimeSpectrumOption 渲染历史切片与选区分析包络', () => {
  const nFft = 256;
  const powers = new Array(nFft).fill(-70);
  const history = new Array(nFft).fill(-45);
  const maxPowers = new Array(nFft).fill(-35);
  const avgPowers = new Array(nFft).fill(-50);

  const option = buildRealtimeSpectrumOption({
    freqMin: 2400e6,
    freqMax: 2410e6,
    rbwHz: 10e6 / nFft,
    powers,
    historyPowers: history,
    historyLabel: '历史切片 (t = -2.5s)',
    windowAnalysisPowers: { maxPowers, avgPowers },
    refLevelDb: 0,
    dynamicRangeDb: 80,
  });

  // 应包含：当前频谱、历史切片、选区峰值包络、选区平均能量
  assert.equal(option.series.length, 4);
  assert.equal(option.series[0].name, '当前频谱');
  assert.equal(option.series[1].name, '历史切片 (t = -2.5s)');
  assert.equal(option.series[1].lineStyle.color, '#c084fc');
  assert.equal(option.series[2].name, '选区峰值包络');
  assert.equal(option.series[2].lineStyle.color, '#34d399');
  assert.equal(option.series[3].name, '选区平均能量');
  assert.equal(option.series[3].lineStyle.color, '#60a5fa');
});

test('findNearestHistoryFrame 准确定位时间距离最近的历史帧', () => {
  const frames = [
    { timestamp: 10.0 },
    { timestamp: 10.5 },
    { timestamp: 11.0 },
    { timestamp: 11.5 },
    { timestamp: 12.0 },
  ];

  assert.equal(findNearestHistoryFrame(frames, 10.1), 0);
  assert.equal(findNearestHistoryFrame(frames, 10.45), 1);
  assert.equal(findNearestHistoryFrame(frames, 11.2), 2);
  assert.equal(findNearestHistoryFrame(frames, 11.8), 4);
  assert.equal(findNearestHistoryFrame([], 10.0), -1);
});

test('calculateWaterfallSelection 正确换算物理频率跨度与时间窗口', () => {
  // 视口未缩放 (0..100)，全频段 2400 ~ 2500 MHz (span 100MHz)
  // 最新时间戳 100.0s，瀑布历史跨度 20s
  // 选区在水平 0.2 ~ 0.5 (即 2420 ~ 2450 MHz, center 2435 MHz, BW 30000 kHz)
  // 选区在垂直 0.1 ~ 0.3 (即 -2s ~ -6s 之前，时间窗口 94.0s ~ 98.0s, duration 4.0s)
  const sel = calculateWaterfallSelection(
    0.2, 0.5, 0.1, 0.3,
    { start: 0, end: 100 },
    2400.0, 2500.0,
    100.0, 20
  );

  assert.equal(sel.freqStartMhz, 2420.0);
  assert.equal(sel.freqEndMhz, 2450.0);
  assert.equal(sel.centerFreqMhz, 2435.0);
  assert.equal(sel.bandwidthKhz, 30000.0);
  assert.equal(sel.timeStart, 94.0);
  assert.equal(sel.timeEnd, 98.0);
  assert.equal(sel.durationS, 4.0);
  assert.equal(sel.xStartPct, 20.0);
  assert.equal(sel.xEndPct, 50.0);
  assert.equal(sel.yStartPct, 10.0);
  assert.equal(sel.yEndPct, 30.0);
});

test('analyzeWindowFrames 计算指定时间窗内的多帧峰值与能量平均谱', () => {
  const nFft = 4;
  const frames = [
    { timestamp: 1.0, powers: [-80, -60, -70, -90], rbw_hz: 1000, freq_min: 2400e6 },
    { timestamp: 1.5, powers: [-70, -40, -65, -85], rbw_hz: 1000, freq_min: 2400e6 }, // 峰值在 bin 1 (-40)
    { timestamp: 2.0, powers: [-75, -50, -68, -88], rbw_hz: 1000, freq_min: 2400e6 },
    { timestamp: 5.0, powers: [-10, -10, -10, -10], rbw_hz: 1000, freq_min: 2400e6 }, // 窗口外，不应被计入
  ];

  const res = analyzeWindowFrames(frames, 1.0, 2.0);
  assert.ok(res);
  assert.equal(res.frameCount, 3);
  assert.equal(res.peakPowerDb, -40);
  // bin 1: max is -40
  assert.equal(res.maxPowers[1], -40);
  // maxPowers[0] should be max(-80, -70, -75) = -70
  assert.equal(res.maxPowers[0], -70);
  // avgPowers should be higher than -60 for bin 1
  assert.ok(res.avgPowers[1] > -50 && res.avgPowers[1] <= -40);
});

test('calculateWaterfallSelection 支持逆向反拉框选与视口缩放状态下的物理频段换算', () => {
  // 视口水平局部放大: 2425 ~ 2475 MHz (start 25%, end 75%)
  // 反拉选择框: x1=0.8, x2=0.2, y1=0.6, y2=0.1
  const sel = calculateWaterfallSelection(
    0.8, 0.2, 0.6, 0.1,
    { start: 25, end: 75 },
    2400.0, 2500.0,
    50.0, 20
  );

  // 视口跨度 50MHz, fullF1 = 0.25 + 0.2 * 0.50 = 0.35 -> 2435 MHz
  // fullF2 = 0.25 + 0.8 * 0.50 = 0.65 -> 2465 MHz
  assert.equal(sel.freqStartMhz, 2435.0);
  assert.equal(sel.freqEndMhz, 2465.0);
  assert.equal(sel.centerFreqMhz, 2450.0);
  assert.equal(sel.bandwidthKhz, 30000.0);

  // y1=0.6, y2=0.1 反拉归一化为 0.1 ~ 0.6 -> timeEnd = 50 - 2 = 48s, timeStart = 50 - 12 = 38s
  assert.equal(sel.timeStart, 38.0);
  assert.equal(sel.timeEnd, 48.0);
  assert.equal(sel.durationS, 10.0);
  assert.equal(sel.xStartPct, 20.0);
  assert.equal(sel.xEndPct, 80.0);
  assert.equal(sel.yStartPct, 10.0);
  assert.equal(sel.yEndPct, 60.0);
});

test('analyzeWindowFrames 空数据或无匹配时间窗口时安全返回 null', () => {
  assert.equal(analyzeWindowFrames([], 10.0, 20.0), null);
  const frames = [{ timestamp: 1.0, powers: [-50], rbw_hz: 100, freq_min: 2400e6 }];
  assert.equal(analyzeWindowFrames(frames, 10.0, 20.0), null);
});


