/**
 * SDR 实时频谱与瀑布图渲染工具：
 * 提供仪器级 ECharts 频谱配置、峰值保持、高对比度热图色标 LUT 及高性能瀑布图 Canvas 绘制。
 */

export interface SpectrumPlotConfig {
  freqMin: number;
  freqMax: number;
  rbwHz: number;
  powers: number[];
  peakHoldPowers?: number[] | null;
  refLevelDb: number;
  dynamicRangeDb: number;
  peakFreq?: number;
  peakPower?: number;
}

/**
 * 构建实时频谱 ECharts Option。
 * 横轴显示真实 RF 频率 (MHz)，纵轴标明相对功率 dBFS。
 */
export function buildRealtimeSpectrumOption(config: SpectrumPlotConfig) {
  const {
    freqMin,
    freqMax,
    rbwHz,
    powers,
    peakHoldPowers,
    refLevelDb,
    dynamicRangeDb,
    peakFreq,
    peakPower,
  } = config;

  if (!powers || !powers.length) {
    return {
      animation: false,
      backgroundColor: 'transparent',
    };
  }

  const nFft = powers.length;
  const minFreqMhz = freqMin / 1e6;
  const maxFreqMhz = freqMax / 1e6;
  const rbwMhz = rbwHz / 1e6;

  // 构造 [freqMhz, powerDbfs] 点阵
  const spectrumPoints: [number, number][] = new Array(nFft);
  for (let i = 0; i < nFft; i++) {
    spectrumPoints[i] = [minFreqMhz + i * rbwMhz, powers[i]];
  }

  const series: any[] = [
    {
      name: '当前频谱',
      type: 'line',
      data: spectrumPoints,
      sampling: 'minmax',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#00e5ff', width: 1.8 },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(0, 229, 255, 0.40)' },
            { offset: 0.6, color: 'rgba(0, 229, 255, 0.10)' },
            { offset: 1, color: 'rgba(0, 229, 255, 0.0)' },
          ],
        },
      },
    },
  ];

  // 叠加峰值保持曲线 (Amber 色)
  if (peakHoldPowers && peakHoldPowers.length === nFft) {
    const holdPoints: [number, number][] = new Array(nFft);
    for (let i = 0; i < nFft; i++) {
      holdPoints[i] = [minFreqMhz + i * rbwMhz, peakHoldPowers[i]];
    }
    series.push({
      name: '峰值保持',
      type: 'line',
      data: holdPoints,
      sampling: 'max',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#f59e0b', width: 1.2, type: 'dashed' },
    });
  }

  // 标记当前可见频段内的最高峰值
  if (peakFreq !== undefined && peakPower !== undefined) {
    series[0].markPoint = {
      symbol: 'pin',
      symbolSize: 28,
      itemStyle: { color: '#00e5ff' },
      label: {
        formatter: () => `${peakPower.toFixed(1)}`,
        fontSize: 10,
        fontFamily: 'monospace',
        color: '#000',
        fontWeight: 'bold',
      },
      data: [
        {
          name: '峰值',
          coord: [peakFreq / 1e6, peakPower],
        },
      ],
    };
  }

  const yMin = Math.floor(refLevelDb - dynamicRangeDb);
  const yMax = Math.ceil(refLevelDb);

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      transitionDuration: 0,
      axisPointer: {
        type: 'cross',
        animation: false,
        lineStyle: { color: '#38bdf8', type: 'dashed', width: 1 },
      },
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      textStyle: { color: '#f0f6fc', fontFamily: 'monospace', fontSize: 11 },
      formatter: (params: any) => {
        if (!params || !params.length) return '';
        const [f, p] = params[0].value;
        let html = `<div class="font-mono text-xs leading-relaxed">
          <div class="text-zinc-400">RF 频率: <span class="text-cyan-400 font-bold">${f.toFixed(4)} MHz</span></div>
          <div class="text-zinc-400">实时功率: <span class="text-emerald-400 font-bold">${p.toFixed(1)} dBFS</span></div>`;
        if (params.length > 1) {
          const holdP = params[1].value[1];
          html += `<div class="text-zinc-400">峰值保持: <span class="text-amber-400 font-bold">${holdP.toFixed(1)} dBFS</span></div>`;
        }
        html += `</div>`;
        return html;
      },
    },
    grid: {
      left: 55,
      right: 25,
      top: 28,
      bottom: 50,
    },
    xAxis: {
      type: 'value',
      min: minFreqMhz,
      max: maxFreqMhz,
      splitNumber: 8,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        fontFamily: 'monospace',
        hideOverlap: true,
        formatter: (val: number) => val.toFixed(3),
      },
      name: 'MHz',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    yAxis: {
      type: 'value',
      min: yMin,
      max: yMax,
      splitNumber: 6,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        fontFamily: 'monospace',
        formatter: '{value}',
      },
      name: 'dBFS',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
      },
      {
        type: 'slider',
        xAxisIndex: 0,
        height: 16,
        bottom: 8,
        borderColor: '#30363d',
        backgroundColor: '#0d1117',
        fillerColor: 'rgba(0, 229, 255, 0.15)',
        handleStyle: { color: '#00e5ff', borderColor: '#38bdf8' },
        textStyle: { color: '#8b949e', fontSize: 9, fontFamily: 'monospace' },
        labelFormatter: (val: number) => val.toFixed(2),
      },
    ],
    series,
  };
}

/**
 * 经典高对比度热图色标查找表 (Turbo / RF Heat Colormap LUT)
 * 将 0..255 的归一化电平平滑映射为 RGB 颜色
 */
export function createTurboColormapLUT(): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  // 控制锚点：[位移 0..1, R, G, B]
  const stops: [number, number, number, number][] = [
    [0.0, 13, 17, 23],     // 深暗底噪 (#0d1117)
    [0.2, 20, 60, 180],    // 幽蓝
    [0.4, 0, 180, 230],    // 高亮青
    [0.6, 20, 220, 90],    // 荧光绿
    [0.8, 250, 210, 0],    // 亮黄
    [1.0, 239, 68, 68],    // 强信号鲜红
  ];

  for (let i = 0; i < 256; i++) {
    const x = i / 255.0;
    let stopIdx = 0;
    while (stopIdx < stops.length - 1 && stops[stopIdx + 1][0] < x) {
      stopIdx++;
    }
    const [x0, r0, g0, b0] = stops[stopIdx];
    const [x1, r1, g1, b1] = stops[Math.min(stopIdx + 1, stops.length - 1)];
    const t = x1 > x0 ? (x - x0) / (x1 - x0) : 0.0;

    lut[i * 3 + 0] = Math.round(r0 + t * (r1 - r0));
    lut[i * 3 + 1] = Math.round(g0 + t * (g1 - g0));
    lut[i * 3 + 2] = Math.round(b0 + t * (b1 - b0));
  }
  return lut;
}

const TURBO_LUT = createTurboColormapLUT();

/**
 * 将单行功率值 (dBFS) 渲染为 ImageData 并绘制到瀑布图 Canvas 顶部。
 * 支持滚动画布、断流/丢帧隔离红线渲染。
 */
export function renderWaterfallRow(
  canvas: HTMLCanvasElement,
  powers: number[],
  refLevelDb: number,
  dynamicRangeDb: number,
  isGap = false,
  rowHeight = 2
) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const width = canvas.width;
  const height = canvas.height;
  if (width <= 0 || height <= 0) return;

  // 1. 将旧瀑布图向下滚动 rowHeight 像素
  ctx.drawImage(canvas, 0, 0, width, height - rowHeight, 0, rowHeight, width, height - rowHeight);

  // 2. 若发生断流、丢帧或频率改变，绘制明显隔离线
  if (isGap) {
    ctx.fillStyle = '#ef4444';
    ctx.fillRect(0, 0, width, rowHeight);
    return;
  }

  // 3. 构建当前行 ImageData
  const rowImg = ctx.createImageData(width, rowHeight);
  const data = rowImg.data;
  const nFft = powers.length;
  const minDb = refLevelDb - dynamicRangeDb;

  for (let x = 0; x < width; x++) {
    // 将屏幕像素 x 线性映射到 FFT bin
    const binIdx = Math.floor((x / width) * nFft);
    const p = powers[binIdx] ?? minDb;
    // 归一化到 0..255
    const norm = Math.max(0, Math.min(255, Math.round(((p - minDb) / dynamicRangeDb) * 255)));

    const r = TURBO_LUT[norm * 3 + 0];
    const g = TURBO_LUT[norm * 3 + 1];
    const b = TURBO_LUT[norm * 3 + 2];

    for (let dy = 0; dy < rowHeight; dy++) {
      const idx = (dy * width + x) * 4;
      data[idx + 0] = r;
      data[idx + 1] = g;
      data[idx + 2] = b;
      data[idx + 3] = 255;
    }
  }

  ctx.putImageData(rowImg, 0, 0);
}
