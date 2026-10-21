import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

async function load(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}
const { connectSpectrumStream } = await load('src/utils/spectrumStream.ts');
const { waterfallTransition, renderWaterfallRow, fitSpectrumLevels, buildRealtimeSpectrumOption, SPECTRUM_PLOT_INSETS } = await load('src/utils/spectrumCharts.ts');

function sockets() {
  const opened = [];
  const createSocket = () => {
    const socket = { readyState: 1, closed: false, send() {}, close() { this.closed = true; this.onclose?.({ code: 1000 }); } };
    opened.push(socket);
    return socket;
  };
  return { opened, createSocket };
}

test('频谱连接过滤旧会话，状态和帧更新不重建连接，卸载后忽略迟到事件', () => {
  const { opened, createSocket } = sockets();
  const frames = [], states = [];
  const dispose = connectSpectrumStream({ sessionId: 'new', url: '/ws', createSocket,
    onFrame: frame => frames.push(frame), onStatus: state => states.push(state), onConnectionError() {} });
  const send = data => opened[0].onmessage({ data: JSON.stringify(data) });
  for (let i = 0; i < 100; i++) send({ session_id: 'new', powers: [i] });
  send({ session_id: 'old', powers: [1000] });
  send({ session_id: 'old', type: 'spectrum_status', state: 'error' });
  send({ session_id: 'new', type: 'spectrum_status', state: 'paused' });
  send({ session_id: 'new', powers: [200], sequence: 200 });
  send({ session_id: 'new', powers: [199], sequence: 199 });
  send({ session_id: 'new', powers: [200], sequence: 200 });
  assert.equal(frames.length, 101);
  assert.equal(states.length, 1);
  assert.equal(opened.length, 1);
  dispose();
  send({ session_id: 'new', powers: [1001] });
  assert.equal(frames.length, 101);
  assert.equal(opened[0].closed, true);
});

test('频谱断线重连，终止状态不重连；旧连接迟到的数据无效', async () => {
  const { opened, createSocket } = sockets();
  const frames = [];
  const dispose = connectSpectrumStream({ sessionId: 's', url: '/ws', createSocket,
    onFrame: frame => frames.push(frame), onStatus() {}, onConnectionError() {} });
  try {
    opened[0].onclose({ code: 1006 });
    await new Promise(resolve => setTimeout(resolve, 550));
    assert.equal(opened.length, 2);
    opened[0].onmessage({ data: JSON.stringify({ session_id: 's', powers: [1] }) });
    assert.equal(frames.length, 0);
    opened[1].onmessage({ data: JSON.stringify({ session_id: 's', type: 'spectrum_status', state: 'error' }) });
    opened[1].onclose({ code: 1000 });
    await new Promise(resolve => setTimeout(resolve, 550));
    assert.equal(opened.length, 2);
  } finally { dispose(); }
});

test('瀑布图使用采集时间推进，断流留白，参数变化保留历史并标记分隔', () => {
  const previous = { center_freq: 2.4e9, sample_rate: 8e6, n_fft: 1024, timestamp: 100 };
  assert.equal(waterfallTransition(previous, { ...previous, timestamp: 101 }).advanceRows, 20);
  assert.equal(waterfallTransition(previous, { ...previous, timestamp: 101 }).gap, true);
  for (const change of [{ n_fft: 2048 }, { center_freq: 2.5e9 }, { sample_rate: 4e6 }]) {
    const transition = waterfallTransition(previous, { ...previous, ...change, timestamp: 100.1 });
    assert.equal(transition.parametersChanged, true);
    assert.equal(transition.gap, true);
    const advanceRows = Math.floor(transition.advanceRows + 1e-6);
    assert.equal(advanceRows, 2);
    const scrolls = [], fills = [];
    const canvas = { width: 4, height: 400, getContext: () => ({
      drawImage: (...args) => scrolls.push(args),
      fillRect: (...args) => fills.push(args),
      createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      putImageData() {},
    }) };
    renderWaterfallRow(canvas, [-80, -20, -20, -80], 0, 80, transition.gap, 1, advanceRows);
    assert.equal(scrolls.length, 1);
    assert.equal(scrolls[0][4], 398); // Copy the existing history, shifting it down by two rows.
    assert.ok(fills.every(rect => rect[3] <= 2)); // Only new rows and the separator are painted.
  }
  // Fractional pixel advances must not be rounded per frame (30 FPS != 30 px/s).
  let pixels = 0;
  for (let i = 0; i < 30; i++) pixels += waterfallTransition(
    { ...previous, timestamp: 100 + i / 30 }, { ...previous, timestamp: 100 + (i + 1) / 30 }).advanceRows;
  assert.ok(Math.abs(pixels - 20) < 1e-6);
});

test('瀑布图缩小绘制时保留单个 FFT bin 的窄峰', () => {
  let rendered;
  const canvas = { width: 2, height: 100, getContext: () => ({
    drawImage() {}, fillRect() {}, createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    putImageData: image => { rendered = image; },
  }) };
  renderWaterfallRow(canvas, [-80, -80, 0, -80, -80, -80, -80, -80], 0, 80);
  assert.ok(rendered.data[0] > 180); // peak inside the first pixel's bin range
  assert.ok(rendered.data[4] < 60); // second pixel remains at the noise floor
});

test('USRP 低电平频谱自动匹配纵轴和瀑布色阶，手动范围保持原值', () => {
  const powers = new Array(1024).fill(-122);
  powers[640] = -98;
  const config = { freqMin: 2478.75e6, freqMax: 2481.25e6 - 2500000 / 1024,
    rbwHz: 2500000 / 1024, powers, refLevelDb: 0, dynamicRangeDb: 80 };
  const manual = buildRealtimeSpectrumOption(config);
  assert.equal(manual.yAxis.min, -80);
  assert.ok(powers.every(value => value < manual.yAxis.min));
  const levels = fitSpectrumLevels(powers, 80);
  const fitted = buildRealtimeSpectrumOption({ ...config, ...levels });
  assert.ok(powers.every(value => value >= fitted.yAxis.min && value <= fitted.yAxis.max));
  let image;
  const canvas = { width: 10, height: 100, getContext: () => ({ drawImage() {}, fillRect() {},
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }), putImageData: value => { image = value; } }) };
  renderWaterfallRow(canvas, powers, levels.refLevelDb, levels.dynamicRangeDb);
  assert.ok(image.data[1] > 60 || image.data[2] > 60); // noise floor is no longer painted black
  assert.equal(fitSpectrumLevels([NaN, Infinity]), null);
});

test('未收到采样时保留频谱坐标轴；有数据后可由实际 ECharts 渲染', async () => {
  const echarts = await import('echarts/core');
  const { LineChart } = await import('echarts/charts');
  const { GridComponent, TooltipComponent, DataZoomComponent } = await import('echarts/components');
  const { SVGRenderer } = await import('echarts/renderers');
  echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, SVGRenderer]);
  const config = { freqMin: 2478.75e6, freqMax: 2481.25e6 - 2500000 / 1024,
    rbwHz: 2500000 / 1024, powers: [], refLevelDb: -80, dynamicRangeDb: 80 };
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 800, height: 400 });
  try {
    chart.setOption(buildRealtimeSpectrumOption(config), { replaceMerge: ['series'] });
    assert.equal(chart.getOption().xAxis[0].name, 'MHz');
    const powers = new Array(1024).fill(-122);
    powers[640] = -98;
    chart.setOption(buildRealtimeSpectrumOption({ ...config, powers }), { replaceMerge: ['series'] });
    assert.equal(chart.getOption().series[0].data.length, 1024);
    assert.match(chart.renderToSVGString(), /<svg/);
    // The frequency plot and slider must match the waterfall's gutters at
    // both narrow and wide widths, including when the frequency range is zoomed.
    for (const width of [360, 1280]) {
      chart.resize({ width });
      for (const [start, end] of [[0, 100], [25, 75]]) {
        const option = buildRealtimeSpectrumOption({ ...config, powers });
        chart.setOption({ ...option, dataZoom: option.dataZoom.map(zoom => ({ ...zoom, start, end })) });
        const min = config.freqMin / 1e6;
        const span = (config.freqMax - config.freqMin) / 1e6;
        const left = chart.convertToPixel({ xAxisIndex: 0 }, min + span * start / 100);
        const right = chart.convertToPixel({ xAxisIndex: 0 }, min + span * end / 100);
        // ECharts rounds zoomed value-axis limits to its pixel precision.
        assert.ok(Math.abs(left - SPECTRUM_PLOT_INSETS.left) < 1);
        assert.ok(Math.abs(right - (width - SPECTRUM_PLOT_INSETS.right)) < 1);
        const slider = chart.getOption().dataZoom.find(zoom => zoom.type === 'slider');
        assert.equal(slider.left, SPECTRUM_PLOT_INSETS.left);
        assert.equal(slider.right, SPECTRUM_PLOT_INSETS.right);
      }
    }
  } finally { chart.dispose(); }
});

test('绘图回调异常显示错误，不再被当成无效 JSON 静默忽略', () => {
  const { opened, createSocket } = sockets();
  const errors = [];
  const dispose = connectSpectrumStream({ sessionId: 's', url: '/ws', createSocket,
    onFrame() { throw new Error('canvas failed'); }, onStatus() {}, onConnectionError: error => errors.push(error) });
  try {
    opened[0].onmessage({ data: JSON.stringify({ session_id: 's', powers: [-120] }) });
    assert.match(errors[0], /canvas failed/);
    opened[0].onmessage({ data: 'invalid json' });
    assert.equal(errors.length, 1);
  } finally { dispose(); }
});
